"""Build the per-college master professor list for the photo re-scrape.

Merges rmp_professors.csv + trace_courses.csv, dedups by normalized name,
attaches college (precompute.COLLEGE_MAP), aliases, and any existing photo URL.
Writes output_data/prof_list.json.
"""
import os
import sys
import csv
import json
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from precompute import normalize_name  # noqa: E402
import photo_research as pr  # noqa: E402


def load_existing_photos(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = str(row.get("name", "")).strip()
            url = str(row.get("image_url", "")).strip()
            if name and url:
                out[normalize_name(name)] = url
    return out


def build_records(data_dir):
    idx = pr.build_alias_index()
    existing = load_existing_photos(os.path.join(data_dir, "professor_photos.csv"))
    seen = {}

    def add(name, dept):
        key = normalize_name(name)
        if not key or key in seen:
            return
        eq = pr.aliases_for(name, idx) - {key}
        seen[key] = {
            "name": name,
            "department": dept or "",
            "college": pr.dept_to_college(dept),
            "aliases": sorted(eq),
            "existing_url": existing.get(key, ""),
        }

    rmp = os.path.join(data_dir, "rmp_professors.csv")
    if os.path.exists(rmp):
        with open(rmp, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                add(str(row.get("name", "")).strip(),
                    str(row.get("department", "")).strip())

    trace = os.path.join(data_dir, "trace_courses.csv")
    if os.path.exists(trace):
        with open(trace, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                first = str(row.get("instructorFirstName", "")).strip()
                last = str(row.get("instructorLastName", "")).strip()
                if first and last:
                    add(f"{first} {last}".title(),
                        str(row.get("departmentName", "")).strip())

    return list(seen.values())


def main():
    p = argparse.ArgumentParser(description="Build per-college professor list")
    p.add_argument("--data-dir", default=None)
    p.add_argument("-o", "--output", default=None)
    args = p.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = args.data_dir or os.path.join(script_dir, "output_data")
    out_path = args.output or os.path.join(data_dir, "prof_list.json")

    records = build_records(data_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)

    from collections import Counter
    counts = Counter(r["college"] for r in records)
    print(f"  {len(records)} unique professors")
    for college, n in counts.most_common():
        print(f"    {n:5d}  {college}")
    print(f"  Wrote {out_path}")


if __name__ == "__main__":
    main()
