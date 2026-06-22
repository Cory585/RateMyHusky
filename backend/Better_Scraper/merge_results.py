"""Merge agent photo-research results into the v2 photo CSV + audit + progress.

Conflict rule: a new verified+plausible URL wins; otherwise keep the existing
photo as fallback; otherwise not_found. Dedup mirrors photo_scrape.save_csv:
drop URLs shared by 3+ profs, or by 2 profs with unrelated surnames.
"""
import os
import sys
import csv
import json
import re
from collections import Counter, defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from precompute import normalize_name, upgrade_image_url  # noqa: E402
import photo_research as pr  # noqa: E402

AUDIT_FIELDS = ["name", "college", "department", "matched_name",
                "source_tier", "confidence", "new_url", "old_url", "status"]


def decide_row(prof, found):
    old = prof.get("existing_url", "") or ""
    base = {
        "name": prof["name"],
        "college": prof.get("college", ""),
        "department": prof.get("department", ""),
        "matched_name": "",
        "source_tier": "",
        "confidence": "",
        "new_url": "",
        "old_url": old,
        "status": "not_found",
    }
    if found and pr.is_plausible_photo_url(found.get("image_url", "")):
        base.update({
            "matched_name": found.get("matched_name", ""),
            "source_tier": found.get("source_tier", ""),
            "confidence": found.get("confidence", ""),
            "new_url": found["image_url"],
            "source_page": found.get("source_page", ""),
            "status": "new",
        })
        return base
    if old:
        base.update({"new_url": old, "status": "kept_old",
                     "source_page": prof.get("existing_source", "")})
        return base
    return base


def _surname(name):
    parts = [p for p in re.sub(r"[^a-z ]", " ", normalize_name(name)).split() if p]
    return parts[-1] if parts else ""


def apply_dedup(rows):
    """Blank new_url (mark as not_found) for URLs shared by 3+ profs or 2 profs
    with unrelated surnames.

    Idempotent: write_outputs() already calls this, so callers normally do not
    call it directly.
    """
    photo_rows = [r for r in rows if r.get("new_url")]
    counts = Counter(upgrade_image_url(r["new_url"]) for r in photo_rows)

    url_surnames = defaultdict(set)
    for r in photo_rows:
        u = upgrade_image_url(r["new_url"])
        if counts[u] == 2:
            url_surnames[u].add(_surname(r["name"]))

    bad = set()
    for u, c in counts.items():
        if c >= 3:
            bad.add(u)
        elif c == 2:
            names = list(url_surnames.get(u, set()))
            if len(names) == 2:
                a, b = names
                if a and b and a not in b and b not in a:
                    bad.add(u)

    for r in rows:
        if r.get("new_url") and upgrade_image_url(r["new_url"]) in bad:
            r["new_url"] = ""
            r["status"] = "not_found"
    return rows


def write_outputs(rows, data_dir):
    rows = apply_dedup(rows)
    os.makedirs(data_dir, exist_ok=True)

    v2 = os.path.join(data_dir, "professor_photos_v2.csv")
    with open(v2, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "image_url", "source_page"])
        w.writeheader()
        for r in rows:
            if r.get("new_url"):
                w.writerow({"name": r["name"], "image_url": r["new_url"],
                            "source_page": r.get("source_page", "")})

    audit = os.path.join(data_dir, "photo_audit.csv")
    with open(audit, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    progress = os.path.join(data_dir, "photo_progress.json")
    with open(progress, "w", encoding="utf-8") as f:
        json.dump({normalize_name(r["name"]): r["status"] for r in rows},
                  f, ensure_ascii=False, indent=1)

    photos = sum(1 for r in rows if r.get("new_url"))
    print(f"  v2: {photos} photos / {len(rows)} professors -> {v2}")
