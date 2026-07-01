"""Embed the `evidence` corpus with BGE-small (local ONNX, INT8) into evidence_embeddings.
Idempotent: only embeds rows missing / version-mismatched / body-changed.

Usage:
    python embed_evidence.py --selftest
    python embed_evidence.py --embed [--batch 256]
"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from load_evidence_to_crdb import connect

MODEL_VERSION = "bge-small-en-v1.5-q8"
_EMBEDDER = None

def load_embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer
        import numpy as np
        name = "BAAI/bge-small-en-v1.5"
        tok = AutoTokenizer.from_pretrained(name)
        model = ORTModelForFeatureExtraction.from_pretrained(name, file_name="model_quantized.onnx", export=True)
        def embed(texts):
            enc = tok(list(texts), padding=True, truncation=True, max_length=256, return_tensors="np")
            emb = model(**enc).last_hidden_state.mean(axis=1)
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

def write_embeddings(conn, tuples, batch=2000):
    from psycopg2.extras import execute_values
    sql = ("INSERT INTO evidence_embeddings (evidence_id, embedding, model_version, body_sha) "
           "VALUES %s ON CONFLICT (evidence_id) DO UPDATE SET "
           "embedding=excluded.embedding, model_version=excluded.model_version, "
           "body_sha=excluded.body_sha, embedded_at=now()")
    # Commit per batch: a mid-run failure during the ~1.1M-row backfill keeps every
    # already-written batch durable (the stage is idempotent, so re-running skips them).
    for i in range(0, len(tuples), batch):
        chunk = [(t[0], str(t[1]), t[2], t[3]) for t in tuples[i:i+batch]]
        with conn.cursor() as cur:
            execute_values(cur, sql, chunk, template="(%s, %s::vector, %s, %s)")
        conn.commit()

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
    parser.add_argument("--batch", type=int, default=256, help="Embedding batch size (default 256)")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if args.embed:
        import psycopg2.extras
        conn = connect()
        def query_fn(sql, params=None):
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params or ())
                return cur.fetchall()

        print("Fetching rows needing embedding...")
        rows = rows_needing_embedding(query_fn, MODEL_VERSION)
        print(f"  {len(rows)} rows to embed")
        if not rows:
            print("Nothing to do.")
            conn.close()
            sys.exit(0)

        embedder = load_embedder()
        total_written = 0
        for i in range(0, len(rows), args.batch):
            chunk = rows[i:i + args.batch]
            tuples = embed_batch(chunk, embedder)
            write_embeddings(conn, tuples)
            total_written += len(tuples)
            print(f"  {total_written}/{len(rows)} embedded")

        print(f"Done. {total_written} rows upserted into evidence_embeddings.")
        conn.close()
        sys.exit(0)

    print("Use --selftest for offline checks or --embed to run the backfill")
    sys.exit(0)

if __name__ == "__main__":
    main()
