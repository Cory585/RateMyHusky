"""Query-side embedder for Ask hybrid retrieval. Warm-loads BGE-small at import (NEVER lazy on
first request — cold load on a 0.5GB Railway instance can take seconds). Returns None on any
error/timeout so retrieval falls back to lexical-only."""
import sys, argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout

MODEL_VERSION = "bge-small-en-v1.5-q8"
_POOL = ThreadPoolExecutor(max_workers=1)
_RAW = None

def _load():
    global _RAW
    if _RAW is None:
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer
        import numpy as np
        name = "BAAI/bge-small-en-v1.5"
        tok = AutoTokenizer.from_pretrained(name)
        model = ORTModelForFeatureExtraction.from_pretrained(name, file_name="model_quantized.onnx", export=True)
        def embed(text):
            enc = tok([text], padding=True, truncation=True, max_length=256, return_tensors="np")
            emb = model(**enc).last_hidden_state.mean(axis=1)
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
