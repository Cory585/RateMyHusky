"""Embed the `evidence` corpus with BGE-small (local ONNX, INT8) into evidence_embeddings.
Idempotent: only embeds rows missing / version-mismatched / body-changed.

Usage:
    python embed_evidence.py --selftest
    python embed_evidence.py --embed [--batch 256]
"""
import argparse, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from load_evidence_to_crdb import connect

MODEL_VERSION = "bge-small-en-v1.5-q8"
_EMBEDDER = None

def load_embedder():
    # Pure onnxruntime (no torch, no optimum) — MUST stay byte-identical (model, pooling,
    # normalization) to backend/query_embedder.py so document vectors match query vectors.
    # Only difference: this batches (list[str] in, list[list[float]] out).
    global _EMBEDDER
    if _EMBEDDER is None:
        import numpy as np
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        import onnxruntime as ort
        repo = "onnx-community/bge-small-en-v1.5-ONNX"
        # Load from the local HF cache without hitting the network: this machine's DNS flakes on
        # huggingface.co (same resolver flakiness as *.cockroachlabs.cloud), which killed the
        # backfill at startup every relaunch. Fall back to a networked fetch only if not cached
        # yet (first run downloads once, then offline forever). Also right for Railway.
        def _get(fn):
            try:
                return hf_hub_download(repo, fn, local_files_only=True)
            except Exception:
                return hf_hub_download(repo, fn)  # not cached yet — fetch once
        onnx_path = _get("onnx/model_quantized.onnx")  # weights in sibling .onnx_data
        _get("onnx/model_quantized.onnx_data")          # ensure the weights sidecar is cached too
        tok = Tokenizer.from_file(_get("tokenizer.json"))
        tok.enable_truncation(max_length=256)
        tok.enable_padding()
        # CAP threads so the backfill leaves the daily-driver machine usable — full-core
        # (os.cpu_count()) pegged the CPU at ~98%. Override with EMBED_THREADS if running on an
        # idle box. Graph optimization is free (no CPU-hog implication).
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("EMBED_THREADS", "3"))
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(onnx_path, sess_options=so, providers=["CPUExecutionProvider"])
        input_names = {i.name for i in sess.get_inputs()}
        def embed(texts):
            encs = tok.encode_batch(list(texts))
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            emb = sess.run(None, feed)[0].mean(axis=1)  # mean-pool last_hidden_state
            emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
            return emb.tolist()
        _EMBEDDER = embed
    return _EMBEDDER

def rows_needing_embedding(query_fn, model_version):
    rows = query_fn("""
        SELECT e.id, e.body, e.body_sha,
               ee.evidence_id AS emb_id, ee.model_version AS emb_ver, ee.body_sha AS emb_sha
        FROM evidence e
        LEFT JOIN evidence_embeddings ee ON ee.evidence_id = e.id
    """, ())
    need = []
    for r in rows:
        if r["emb_id"] is None or r["emb_ver"] != model_version or r["emb_sha"] != r["body_sha"]:
            need.append(r)
    return need

def embed_batch(rows, embedder):
    vecs = embedder([r["body"] for r in rows])
    return [(r["id"], v, MODEL_VERSION, r["body_sha"]) for r, v in zip(rows, vecs)]

def write_embeddings(conn, tuples, batch=500):
    import time, psycopg2
    from psycopg2.extras import execute_values
    sql = ("INSERT INTO evidence_embeddings (evidence_id, embedding, model_version, body_sha) "
           "VALUES %s ON CONFLICT (evidence_id) DO UPDATE SET "
           "embedding=excluded.embedding, model_version=excluded.model_version, "
           "body_sha=excluded.body_sha, embedded_at=now()")
    # Commit per batch (durable + idempotent re-run). CockroachDB uses SERIALIZABLE isolation and
    # asks clients to retry transient SerializationFailure (40001) with backoff — vector-index
    # partition updates are a contention hotspot, so retry is mandatory here just like the loader.
    for i in range(0, len(tuples), batch):
        chunk = [(t[0], str(t[1]), t[2], t[3]) for t in tuples[i:i+batch]]
        for attempt in range(6):
            try:
                with conn.cursor() as cur:
                    execute_values(cur, sql, chunk, template="(%s, %s::vector, %s, %s)")
                conn.commit()
                break
            except psycopg2.errors.SerializationFailure:
                conn.rollback()
                if attempt == 5:
                    raise
                time.sleep(0.5 * (2 ** attempt))

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    # rows_needing_embedding: missing, version-mismatch, body-changed all selected; up-to-date skipped
    def q(sql, params=None):
        return [
            {"id": "e1", "body": "a", "body_sha": "sha_a", "emb_id": None, "emb_ver": None, "emb_sha": None},        # missing
            {"id": "e2", "body": "b", "body_sha": "sha_b", "emb_id": "e2", "emb_ver": "old", "emb_sha": "sha_b"},     # version mismatch
            {"id": "e3", "body": "c2", "body_sha": "sha_c2", "emb_id": "e3", "emb_ver": MODEL_VERSION, "emb_sha": "sha_c1"},  # body changed
            {"id": "e4", "body": "d", "body_sha": "sha_d", "emb_id": "e4", "emb_ver": MODEL_VERSION, "emb_sha": "sha_d"},  # up to date
        ]
    need = rows_needing_embedding(q, MODEL_VERSION)
    ids = {r["id"] for r in need}
    check("re-embeds missing/mismatch/changed, skips up-to-date", ids == {"e1", "e2", "e3"})

    # embed_batch maps each row to a (id, vector, version, sha) tuple via the embedder
    def fake_embedder(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]
    tuples = embed_batch([{"id": "e1", "body": "a", "body_sha": "sha_a"}], fake_embedder)
    check("embed_batch returns id+vector+version+sha",
          tuples[0][0] == "e1" and tuples[0][2] == MODEL_VERSION and tuples[0][3] == "sha_a"
          and tuples[0][1] == [0.1, 0.2, 0.3])

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    parser = argparse.ArgumentParser(description="Embed evidence corpus into evidence_embeddings")
    parser.add_argument("--selftest", action="store_true", help="Offline checks (no model/DB)")
    parser.add_argument("--embed", action="store_true", help="Run embedding backfill")
    # bs=32 is the measured CPU throughput sweet spot; larger batches pad every sequence to the
    # longest in the batch and get SLOWER (bs=256 crawled at ~36 rows/sec, bs=32 hits ~1100).
    parser.add_argument("--batch", type=int, default=32, help="Embedding batch size (default 32)")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if args.embed:
        import psycopg2, psycopg2.extras
        # STREAMING (server-side cursor, itersize) so we never materialize all ~1.4M rows into RAM.
        # Two connections: one streams the work list, one writes embeddings (a named cursor can't
        # share a connection with writes).
        NEEDS_SQL = """
            SELECT e.id, e.body, e.body_sha
            FROM evidence e
            LEFT JOIN evidence_embeddings ee ON ee.evidence_id = e.id
            WHERE ee.evidence_id IS NULL
               OR ee.model_version <> %s
               OR ee.body_sha IS DISTINCT FROM e.body_sha
        """
        embedder = load_embedder()
        t_start = time.time()
        total_written = 0

        # Self-healing outer loop: the CockroachDB host has flaky DNS (see crdb-dns-retry gotcha),
        # so a connection CAN drop mid-stream. On any connection error we reconnect and RE-ISSUE
        # the query — because it only returns not-yet-embedded rows, it naturally resumes from
        # the DB's state (no lost/redone work; upsert is idempotent). Loops until 0 rows remain.
        # This makes the overnight run truly fire-and-forget through DNS blips.
        while True:
            read_conn = write_conn = None
            did_work = False
            try:
                read_conn = connect()
                write_conn = connect()
                cur = read_conn.cursor(name="embed_stream", cursor_factory=psycopg2.extras.RealDictCursor)
                cur.itersize = 2000
                cur.execute(NEEDS_SQL, (MODEL_VERSION,))
                batch = []
                def flush(rows):
                    nonlocal total_written
                    if not rows:
                        return
                    write_embeddings(write_conn, embed_batch(rows, embedder))
                    total_written += len(rows)
                    if total_written % 1000 < args.batch:
                        el = time.time() - t_start
                        rate = total_written / el if el > 0 else 0
                        print(f"  {total_written} embedded  ({rate:.0f} rows/sec, {el/60:.0f} min elapsed)", flush=True)
                for row in cur:
                    did_work = True
                    batch.append({"id": row["id"], "body": row["body"], "body_sha": row["body_sha"]})
                    if len(batch) >= args.batch:
                        flush(batch); batch = []
                flush(batch)
                cur.close()
                break  # stream exhausted cleanly -> everything embedded
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                # connection dropped (DNS/network). Reconnect + re-stream (resumes from DB state).
                print(f"  [connection dropped: {str(e).splitlines()[0][:80]}] reconnecting + resuming...", flush=True)
                time.sleep(3)
                if not did_work:
                    # made no progress this pass before failing again — back off harder to avoid a
                    # tight fail loop during an extended DNS outage.
                    time.sleep(15)
                continue
            finally:
                for cn in (read_conn, write_conn):
                    try:
                        if cn: cn.close()
                    except Exception:
                        pass

        print(f"Done. {total_written} rows upserted into evidence_embeddings.", flush=True)
        sys.exit(0)

    print("Use --selftest for offline checks or --embed to run the backfill")
    sys.exit(0)

if __name__ == "__main__":
    main()
