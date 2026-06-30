"""
Collect per-batch verify verdicts (keyed by within-batch index) into a single
verify_verdicts.csv keyed by mention_id, using verify_targets.csv as the join.

Agents cite a short per-batch index (#1, #2, ...) instead of the opaque 16-hex
mention_id, which they transcribe unreliably. This script resolves each
(batch, idx) back to its mention_id deterministically.

Usage
-----
    python collect_verdicts.py            # join verdicts/*.csv -> verify_verdicts.csv
    python collect_verdicts.py --selftest # offline checks, then exit
"""

import argparse
import csv
import glob
import os
import re
import sys

csv.field_size_limit(10 * 1024 * 1024)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "reddit_data")

VERDICT_COLS = ["mention_id", "verdict", "reassign_slug", "evidence_quote", "confidence"]


def load_idx_map(targets_path):
    """Return {(batch:int, idx:int): mention_id} from verify_targets.csv."""
    out = {}
    with open(targets_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[(int(row["batch"]), int(row["idx"]))] = row["mention_id"]
    return out


_BATCH_RE = re.compile(r"batch_(\d+)\.csv$")


def batch_num_from_path(path):
    m = _BATCH_RE.search(os.path.basename(path))
    return int(m.group(1)) if m else None


def join_batch(batch_num, verdict_rows, idx_map):
    """Map a batch's idx-keyed verdict rows to mention_id-keyed rows.

    Returns (resolved_rows, unresolved_idxs). A verdict whose (batch, idx) is
    not in idx_map is unresolved (agent cited a nonexistent index).
    """
    resolved = []
    unresolved = []
    for r in verdict_rows:
        key = (batch_num, int(r["idx"]))
        mid = idx_map.get(key)
        if mid is None:
            unresolved.append(int(r["idx"]))
            continue
        resolved.append({
            "mention_id": mid,
            "verdict": r["verdict"].strip(),
            "reassign_slug": r.get("reassign_slug", "").strip(),
            "evidence_quote": r.get("evidence_quote", ""),
            "confidence": r.get("confidence", "").strip(),
        })
    return resolved, unresolved


def collect(data_dir=None):
    d = data_dir or DATA
    idx_map = load_idx_map(os.path.join(d, "verify_targets.csv"))
    out_rows = []
    problems = []
    for path in sorted(glob.glob(os.path.join(d, "verdicts", "batch_*.csv"))):
        bn = batch_num_from_path(path)
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        resolved, unresolved = join_batch(bn, rows, idx_map)
        out_rows.extend(resolved)
        if unresolved:
            problems.append((bn, unresolved))

    out_path = os.path.join(d, "verify_verdicts.csv")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=VERDICT_COLS)
        w.writeheader()
        w.writerows(out_rows)

    print(f"collected {len(out_rows)} verdicts from {len(glob.glob(os.path.join(d, 'verdicts', 'batch_*.csv')))} batch files")
    if problems:
        print("WARNING: unresolved indices (agent cited a nonexistent #) in:")
        for bn, idxs in problems:
            print(f"  batch {bn}: {idxs}")
    return 1 if problems else 0


def selftest() -> int:
    import tempfile

    fails = []

    def check(name, cond):
        print(("PASS" if cond else "FAIL"), name)
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        # verify_targets.csv: batch 1 has idx 1,2 ; batch 2 has idx 1
        with open(os.path.join(tmp, "verify_targets.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["idx", "mention_id", "batch", "professor_slug", "method"])
            w.writeheader()
            w.writerow({"idx": "1", "mention_id": "aaa", "batch": "1", "professor_slug": "x", "method": "exact_full"})
            w.writerow({"idx": "2", "mention_id": "bbb", "batch": "1", "professor_slug": "y", "method": "lastname"})
            w.writerow({"idx": "1", "mention_id": "ccc", "batch": "2", "professor_slug": "z", "method": "conv_context"})

        idx_map = load_idx_map(os.path.join(tmp, "verify_targets.csv"))
        check("idx_map resolves (1,2)->bbb", idx_map[(1, 2)] == "bbb")
        check("idx_map distinguishes batches: (2,1)->ccc", idx_map[(2, 1)] == "ccc")

        # batch file numbering
        check("batch_num_from_path parses 007", batch_num_from_path("/p/batch_007.csv") == 7)

        # join: batch 1 verdicts citing #1 and #2
        v1 = [
            {"idx": "1", "verdict": "keep", "reassign_slug": "", "evidence_quote": "good", "confidence": "high"},
            {"idx": "2", "verdict": "drop", "reassign_slug": "", "evidence_quote": "off topic", "confidence": "high"},
            {"idx": "9", "verdict": "keep", "reassign_slug": "", "evidence_quote": "x", "confidence": "low"},  # bad idx
        ]
        resolved, unresolved = join_batch(1, v1, idx_map)
        by_mid = {r["mention_id"]: r for r in resolved}
        check("join maps #1 -> aaa", "aaa" in by_mid and by_mid["aaa"]["verdict"] == "keep")
        check("join maps #2 -> bbb (drop)", by_mid["bbb"]["verdict"] == "drop")
        check("nonexistent idx #9 is unresolved", unresolved == [9])
        check("resolved excludes the bad idx", len(resolved) == 2)

        # same idx, different batch resolves to different mention
        v2 = [{"idx": "1", "verdict": "keep", "reassign_slug": "", "evidence_quote": "z good", "confidence": "high"}]
        r2, _ = join_batch(2, v2, idx_map)
        check("batch 2 #1 -> ccc (not aaa)", r2[0]["mention_id"] == "ccc")

    print("ALL PASS" if not fails else f"{len(fails)} FAIL")
    return 1 if fails else 0


def main():
    p = argparse.ArgumentParser(description="Join per-batch idx verdicts into verify_verdicts.csv.")
    p.add_argument("--selftest", action="store_true", help="Run offline checks and exit")
    args = p.parse_args()

    if args.selftest:
        sys.exit(selftest())
    sys.exit(collect())


if __name__ == "__main__":
    main()
