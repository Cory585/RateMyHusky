"""Pure IR metric functions for the Ask RAG evals. Stdlib only; no I/O.

Definitions (fixed by docs/superpowers/specs/2026-07-28-rag-evals-design.md):
- nDCG@k: DCG = sum over ranks i=1..k of (2^rel_i - 1) / log2(i + 1); ideal DCG from
  the unit's full labeled pool sorted best-first. None when the pool has no rel>0 label.
- recall@k(threshold): |run top-k with rel>=threshold| / |pool with rel>=threshold|;
  None when the pool has none at that threshold.
- MRR(threshold): 1 / rank of first run item with rel>=threshold; 0.0 when the run has
  none but the pool does; None when the pool has none.
- precision@k(threshold): |run top-k with rel>=threshold| / min(k, len(run)); None on an
  empty run (an entity with only 5 evidence rows has 5 prompt slots, not 8 - so the
  denominator is the slots that actually existed).
A None metric means "this unit can't be scored on this metric"; aggregate() skips and
counts Nones instead of averaging them as 0.
"""
import sys, math, argparse


def dcg_at_k(rels, k):
    return sum((2 ** r - 1) / math.log2(i + 1) for i, r in enumerate(rels[:k], 1))


def ndcg_at_k(run_rels, pool_rels, k):
    ideal = dcg_at_k(sorted(pool_rels, reverse=True), k)
    if ideal == 0:
        return None
    return dcg_at_k(run_rels, k) / ideal


def recall_at_k(run_rels, pool_rels, k, threshold=1):
    total = sum(1 for r in pool_rels if r >= threshold)
    if total == 0:
        return None
    return sum(1 for r in run_rels[:k] if r >= threshold) / total


def mrr(run_rels, pool_rels, threshold=2):
    if not any(r >= threshold for r in pool_rels):
        return None
    for i, r in enumerate(run_rels, 1):
        if r >= threshold:
            return 1.0 / i
    return 0.0


def precision_at_k(run_rels, k, threshold=1):
    if not run_rels:
        return None
    return sum(1 for r in run_rels[:k] if r >= threshold) / min(k, len(run_rels))


def aggregate(values):
    """Macro-average skipping None; returns {mean, n_scored, n_skipped}."""
    scored = [v for v in values if v is not None]
    return {"mean": (sum(scored) / len(scored)) if scored else None,
            "n_scored": len(scored), "n_skipped": len(values) - len(scored)}


def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)
    def approx(a, b, tol=1e-6):
        return a is not None and abs(a - b) <= tol

    # Hand-computed worked example. run=[2,1,0,2], pool=[2,2,1,0,0], k=4.
    # DCG  = (2^2-1)/log2(2) + (2^1-1)/log2(3) + 0 + (2^2-1)/log2(5)
    #      = 3.0 + 0.63092975 + 0 + 1.29202967 = 4.92295943
    # IDCG = ideal order [2,2,1,0]: 3.0 + 3/log2(3) + 1/log2(4) + 0
    #      = 3.0 + 1.89278926 + 0.5 = 5.39278926
    check("dcg worked example", approx(dcg_at_k([2, 1, 0, 2], 4), 4.92295943, 1e-6))
    check("idcg worked example", approx(dcg_at_k([2, 2, 1, 0, 0], 4), 5.39278926, 1e-6))
    check("ndcg worked example", approx(ndcg_at_k([2, 1, 0, 2], [2, 2, 1, 0, 0], 4), 0.91287807, 1e-4))
    check("dcg of empty run is 0", dcg_at_k([], 8) == 0.0)
    check("dcg truncates at k", dcg_at_k([2, 2, 2], 1) == 3.0)
    check("perfect ranking -> ndcg 1.0", ndcg_at_k([2, 2, 1], [2, 2, 1], 8) == 1.0)
    check("pool with no positive labels -> ndcg None", ndcg_at_k([0, 0], [0, 0, 0], 8) is None)

    check("recall@4 thr1 worked example", recall_at_k([2, 1, 0, 2], [2, 2, 1, 0, 0], 4, 1) == 1.0)
    check("recall@4 thr2 worked example", recall_at_k([2, 1, 0, 2], [2, 2, 1, 0, 0], 4, 2) == 1.0)
    check("recall partial", recall_at_k([0, 0, 1], [2, 1, 0], 3, 1) == 0.5)
    check("recall strict miss is 0.0", recall_at_k([0, 0, 1], [2, 1, 0], 3, 2) == 0.0)
    check("recall None when pool lacks threshold", recall_at_k([1], [1, 0], 8, 2) is None)

    check("mrr thr2 first hit rank 3", approx(mrr([0, 1, 2], [2, 1, 0], 2), 1 / 3))
    check("mrr thr1 first hit rank 2", approx(mrr([0, 1, 2], [2, 1, 0], 1), 1 / 2))
    check("mrr retrieved none but pool has -> 0.0", mrr([0, 0], [2], 2) == 0.0)
    check("mrr pool lacks threshold -> None", mrr([1], [1], 2) is None)

    check("precision@8 thr1 = 3/4 (short run denominator)", precision_at_k([2, 1, 0, 2], 8, 1) == 0.75)
    check("precision@8 thr2 = 2/4", precision_at_k([2, 1, 0, 2], 8, 2) == 0.5)
    check("precision empty run -> None", precision_at_k([], 8) is None)
    check("precision cutoff k=1", precision_at_k([0, 2], 1, 1) == 0.0)

    agg = aggregate([1.0, 0.5, None])
    check("aggregate skips None", approx(agg["mean"], 0.75) and agg["n_scored"] == 2 and agg["n_skipped"] == 1)
    check("aggregate all None -> mean None", aggregate([None, None])["mean"] is None)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--selftest", action="store_true")
    if p.parse_args().selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")
