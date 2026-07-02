"""Query-side embedder for Ask hybrid retrieval. Warm-loads BGE-small at import (NEVER lazy on
first request — cold load on a 0.5GB Railway instance can take seconds). Returns None on any
error/timeout so retrieval falls back to lexical-only."""
import sys, argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout

MODEL_VERSION = "bge-small-en-v1.5-q8"
_POOL = ThreadPoolExecutor(max_workers=1)
_RAW = None

def _load():
    # Pure onnxruntime — no torch, no optimum. The optimum wrapper (a) crashes on some
    # torch/onnxruntime version combos (torch.int4) and (b) drags multi-GB torch that can't fit
    # Railway's 0.5GB tier. onnxruntime + tokenizers loads the ~30MB quantized ONNX directly.
    # MUST stay byte-identical (model, pooling, normalization) to scraper/embed_evidence.py so
    # query and document vectors are comparable.
    global _RAW
    if _RAW is None:
        import numpy as np
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        import onnxruntime as ort
        repo = "onnx-community/bge-small-en-v1.5-ONNX"
        # Load from the local HF cache without touching the network (this host's DNS flakes on
        # huggingface.co); fetch once only if not cached. Kept identical to embed_evidence.py.
        def _get(fn):
            try:
                return hf_hub_download(repo, fn, local_files_only=True)
            except Exception:
                return hf_hub_download(repo, fn)
        onnx_path = _get("onnx/model_quantized.onnx")  # weights in sibling .onnx_data
        _get("onnx/model_quantized.onnx_data")
        tok = Tokenizer.from_file(_get("tokenizer.json"))
        tok.enable_truncation(max_length=256)
        tok.enable_padding()
        # Capped threads (kept identical to scraper/embed_evidence.py). The query path embeds ONE
        # short string per request, so a few threads is plenty and won't hog a small Railway box.
        import os
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("EMBED_THREADS", "3"))
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(onnx_path, sess_options=so, providers=["CPUExecutionProvider"])
        input_names = {i.name for i in sess.get_inputs()}
        def embed(text):
            enc = tok.encode(text)
            ids = np.array([enc.ids], dtype=np.int64)
            mask = np.array([enc.attention_mask], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            emb = sess.run(None, feed)[0].mean(axis=1)  # mean-pool last_hidden_state
            emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
            return emb[0].tolist()
        _RAW = embed
    return _RAW

def _embed_with_timeout(fn, text, timeout_s=2.0):
    try:
        return _POOL.submit(fn, text).result(timeout=timeout_s)
    except (_FTimeout, Exception):
        return None

def embed_query(text):
    if not text or not text.strip():
        return None
    try:
        return _embed_with_timeout(_load(), text, timeout_s=2.0)
    except Exception:
        return None

# Warm-load at import so the first real request is fast (best-effort; failure is non-fatal).
try:
    _load()
except Exception:
    pass


def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    check("model_version matches document side", MODEL_VERSION == "bge-small-en-v1.5-q8")

    ok = _embed_with_timeout(lambda t: [0.1, 0.2], "q", timeout_s=1.0)
    check("returns vector on success", ok == [0.1, 0.2])

    def boom(t): raise RuntimeError("model down")
    check("returns None on embedder error", _embed_with_timeout(boom, "q", timeout_s=1.0) is None)

    import time as _t
    def slow(t): _t.sleep(2.0); return [9.9]
    check("returns None on timeout", _embed_with_timeout(slow, "q", timeout_s=0.2) is None)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(selftest())
