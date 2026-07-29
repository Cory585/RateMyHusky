"""Prints a metric-by-metric diff of two eval run folders.
Run: python backend/rag/eval/compare_runs.py backend/rag/eval/runs/<A> backend/rag/eval/runs/<B>"""
import os, sys, argparse

from eval_common import load_json


def _flatten(run_dir):
    out = {}
    rm = load_json(os.path.join(run_dir, "retrieval_metrics.json"), None)
    if rm:
        out.update(rm["summary"])
        for mode, metrics in rm.get("by_mode", {}).items():
            for m, agg in metrics.items():
                out[f"{mode}.{m}"] = agg
    g = load_json(os.path.join(run_dir, "grades.json"), None)
    if g and g.get("summary"):
        for k, v in g["summary"].items():
            if k != "graded":
                out[f"gen.{k}"] = {"mean": v}
    return out


def diff_table(a, b, name_a, name_b):
    keys = list(a) + [k for k in b if k not in a]
    lines = [f"{'metric':32s} {name_a:>10s} {name_b:>10s} {'delta':>9s}"]
    for k in keys:
        ma, mb = a.get(k, {}).get("mean"), b.get(k, {}).get("mean")
        fa = "n/a" if ma is None else f"{ma:.4f}"
        fb = "n/a" if mb is None else f"{mb:.4f}"
        d = "" if (ma is None or mb is None) else f"{mb - ma:+.4f}"
        lines.append(f"{k:32s} {fa:>10s} {fb:>10s} {d:>9s}")
    return "\n".join(lines)


def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    a = {"ndcg@8": {"mean": 0.80}, "recall@8_rel2": {"mean": 0.70}}
    b = {"ndcg@8": {"mean": 0.85}, "recall@8_rel2": {"mean": None}}
    t = diff_table(a, b, "old", "new")
    check("table shows both runs and delta", "ndcg@8" in t and "0.8000" in t
          and "0.8500" in t and "+0.0500" in t)
    check("None renders as n/a with no delta", "n/a" in t)

    a2 = {**a, "gen.faithfulness": {"mean": 0.82}}
    t2 = diff_table(a2, b, "old", "new")
    check("a-only key kept (union, not intersection)",
          "gen.faithfulness" in t2 and "0.8200" in t2)
    line = next(l for l in t2.splitlines() if l.startswith("gen.faithfulness"))
    check("a-only key has n/a b-value and empty delta cell",
          line.rstrip().endswith("n/a") and t2.count("+0.0500") == 1)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("runs", nargs="*", help="two run directories")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if len(args.runs) != 2:
        sys.exit("usage: compare_runs.py <run_dir_A> <run_dir_B>")
    a, b = (_flatten(r) for r in args.runs)
    print(diff_table(a, b, os.path.basename(args.runs[0])[:10], os.path.basename(args.runs[1])[:10]))


if __name__ == "__main__":
    main()
