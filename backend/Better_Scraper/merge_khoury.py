"""Merge the agent photo-research batch/result pairs for one college into the
v2 photo CSV + audit + progress.

Each khoury_batches/batch_NN.json holds the professors assigned to a batch
(name, college, department, aliases, existing_url); the sibling result_NN.json
holds the agent's findings in the same order. We join by index, run
merge_results.decide_row per professor, apply the drop-old-URLs policy, then
write_outputs (which dedups + emits professor_photos_v2.csv, photo_audit.csv,
photo_progress.json).

Usage:
    python merge_khoury.py [--batches-dir output_data/khoury_batches]
                           [--data-dir output_data]
"""
import os
import sys
import json
import glob
import argparse
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import merge_results as mr  # noqa: E402


def load_pairs(batches_dir):
    """Yield (prof, found) tuples joined by index from every batch/result pair.

    Skips a batch with a warning if its result file is missing or the row
    counts disagree (so a half-written result never silently corrupts output).
    """
    rows = []
    for bf in sorted(glob.glob(os.path.join(batches_dir, "batch_*.json"))):
        n = os.path.basename(bf).split("_")[1].split(".")[0]
        rf = os.path.join(batches_dir, f"result_{n}.json")
        if not os.path.exists(rf):
            print(f"  WARN: no result for batch_{n}, skipping")
            continue
        profs = json.load(open(bf, encoding="utf-8"))
        found = json.load(open(rf, encoding="utf-8"))
        if len(profs) != len(found):
            print(f"  WARN: batch_{n} len {len(profs)} != result len {len(found)}, skipping")
            continue
        for prof, f in zip(profs, found):
            rows.append((prof, f))
    return rows


def build_rows(batches_dir):
    rows = [mr.decide_row(prof, f) for prof, f in load_pairs(batches_dir)]
    rows = mr.drop_old_urls(rows)
    return rows


def main():
    p = argparse.ArgumentParser(description="Merge a college's photo-research batches into v2 outputs")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    p.add_argument("--batches-dir",
                   default=os.path.join(script_dir, "output_data", "khoury_batches"))
    p.add_argument("--data-dir",
                   default=os.path.join(script_dir, "output_data"))
    args = p.parse_args()

    rows = build_rows(args.batches_dir)
    print(f"  {len(rows)} professor records")
    print(f"  status (pre-dedup): {dict(Counter(r['status'] for r in rows))}")
    print(f"  linkedin_url logged: {sum(1 for r in rows if r.get('linkedin_url'))}")

    mr.write_outputs(rows, args.data_dir)


if __name__ == "__main__":
    main()
