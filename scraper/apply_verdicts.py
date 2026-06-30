"""
Apply human/agent keep/drop/reassign verdicts to the Reddit mentions corpus.

Reads verify_verdicts.csv + verify_targets.csv, validates them against the
professor catalog, then rewrites reddit_mentions.verified.csv (resolved
mentions with audit columns) and prunes sentiment score files to only
surviving keeps.

Usage
-----
    python apply_verdicts.py          # apply verdicts and write outputs
    python apply_verdicts.py --selftest  # offline checks, then exit
"""

import argparse
import csv
import datetime
import os
import shutil
import sys

csv.field_size_limit(10 * 1024 * 1024)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "reddit_data")

AUDIT_COLS = ["verify_verdict", "verify_quote", "verify_confidence", "verify_orig_slug"]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_resolved_mentions(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["status"] == "resolved":
                out.append(row)
    return out


def load_verdicts(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["mention_id"]] = row
    return out


def load_valid_slugs(backup_path=None):
    import match_professors as mp
    path = backup_path or os.path.join(
        HERE, "..", "backend", "backups", "ratemyhusky_new_20260602T001500Z.sql.gz")
    return {p.slug for p in mp.load_catalog(path) if p.slug}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(verdicts, target_ids, valid_slugs):
    vid = set(verdicts)
    missing = sorted(target_ids - vid)
    unknown = sorted(vid - target_ids)
    bad_reassign = sorted(
        mid for mid, v in verdicts.items()
        if v["verdict"] == "reassign" and v.get("reassign_slug", "") not in valid_slugs)
    return {"missing": missing, "unknown": unknown, "bad_reassign": bad_reassign}


# ---------------------------------------------------------------------------
# Apply verdicts
# ---------------------------------------------------------------------------

def apply_to_mentions(mentions, verdicts, slug_info):
    out = []
    counts = {"kept": 0, "dropped": 0, "reassigned": 0, "skipped": 0}
    for m in mentions:
        v = verdicts.get(m["mention_id"])
        if v is None:
            counts["skipped"] += 1
            continue
        if v["verdict"] == "drop":
            counts["dropped"] += 1
            continue
        row = dict(m)
        row["verify_verdict"] = v["verdict"]
        row["verify_quote"] = v.get("evidence_quote", "")
        row["verify_confidence"] = v.get("confidence", "")
        row["verify_orig_slug"] = ""
        if v["verdict"] == "reassign":
            new_slug = v["reassign_slug"]
            name, name_key = slug_info.get(new_slug, (new_slug, new_slug.replace("-", " ")))
            row["verify_orig_slug"] = m["professor_slug"]
            row["professor_slug"] = new_slug
            row["professor_name"] = name
            row["name_key"] = name_key
            row["method"] = "reassigned"
            counts["reassigned"] += 1
        else:
            counts["kept"] += 1
        out.append(row)
    return out, counts


# ---------------------------------------------------------------------------
# Score carry-forward
# ---------------------------------------------------------------------------

def keep_keys_from(verified_rows):
    return {(r["source_type"], r["source_id"], r["professor_slug"])
            for r in verified_rows if r.get("verify_verdict") == "keep"}


def carry_forward_scores(score_rows, keep_keys):
    survivors = []
    discarded = 0
    for r in score_rows:
        key = (r["source_type"], r["source_id"], r["professor_slug"])
        if key in keep_keys:
            survivors.append(r)
        else:
            discarded += 1
    return survivors, discarded


# ---------------------------------------------------------------------------
# Backup helper
# ---------------------------------------------------------------------------

def _backup(path, data_dir=None):
    if os.path.exists(path):
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(data_dir or DATA, "_backup", os.path.basename(path).replace(".csv", f".{ts}.csv"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(path, dst)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(data_dir=None, valid_slugs=None, slug_info=None):
    d = data_dir or DATA
    mpath = os.path.join(d, "reddit_mentions.csv")
    mentions = load_resolved_mentions(mpath)
    verdicts = load_verdicts(os.path.join(d, "verify_verdicts.csv"))
    targets = load_verdicts(os.path.join(d, "verify_targets.csv"))

    if valid_slugs is None:
        valid_slugs = load_valid_slugs()

    res = validate(verdicts, set(targets), valid_slugs)
    if res["missing"] or res["unknown"] or res["bad_reassign"]:
        print("VALIDATION FAILED")
        print("  missing:", res["missing"][:10], f"(+{max(0, len(res['missing']) - 10)} more)")
        print("  unknown:", res["unknown"][:10])
        print("  bad_reassign:", res["bad_reassign"][:10])
        return 1

    if slug_info is None:
        import match_professors as mp
        bk = os.path.join(HERE, "..", "backend", "backups", "ratemyhusky_new_20260602T001500Z.sql.gz")
        slug_info = {p.slug: (p.name, p.name_key) for p in mp.load_catalog(bk) if p.slug}

    verified, counts = apply_to_mentions(mentions, verdicts, slug_info)

    with open(mpath, encoding="utf-8") as f:
        base_cols = csv.DictReader(f).fieldnames
    out_cols = list(base_cols) + [c for c in AUDIT_COLS if c not in base_cols]

    _backup(mpath, d)
    out_path = os.path.join(d, "reddit_mentions.verified.csv")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(verified)

    keep_keys = keep_keys_from(verified)
    total_discarded = 0
    for sf in ("sentiment_scores.csv", "sentiment_scores_cc.csv"):
        sp = os.path.join(d, sf)
        if not os.path.exists(sp):
            continue
        with open(sp, encoding="utf-8") as f:
            rdr = csv.DictReader(f)
            cols = rdr.fieldnames
            rows = list(rdr)
        survivors, disc = carry_forward_scores(rows, keep_keys)
        total_discarded += disc
        _backup(sp, d)
        with open(sp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(survivors)

    print("=== apply_verdicts report ===")
    print(f"verdicts: kept {counts['kept']}  dropped {counts['dropped']}  reassigned {counts['reassigned']}")
    print(f"resolved mentions not verified (no verdict): {counts['skipped']}")
    print(f"resolved mentions: {len(mentions)} -> {len(verified)} verified")
    print(f"scores discarded (drop+reassign of previously-scored): {total_discarded}")
    print("Next: regenerate tasks from reddit_mentions.verified.csv and run --progress per tier to get the exact unscored gap.")
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest() -> int:
    import tempfile

    fails = []

    def check(name, cond):
        print(("PASS" if cond else "FAIL"), name)
        if not cond:
            fails.append(name)

    # --- Task 4: validate ---
    verdicts = {
        "m1": {"mention_id": "m1", "verdict": "keep", "reassign_slug": "", "evidence_quote": "great prof", "confidence": "high"},
        "m2": {"mention_id": "m2", "verdict": "reassign", "reassign_slug": "weiling-liu", "evidence_quote": "they mean weiling", "confidence": "medium"},
        "m3": {"mention_id": "m3", "verdict": "reassign", "reassign_slug": "not-a-real-slug", "evidence_quote": "x", "confidence": "low"},
    }
    target_ids = {"m1", "m2", "m3", "m4"}
    valid = {"weiling-liu", "john-rachlin"}
    res = validate(verdicts, target_ids, valid)
    check("missing detects m4", res["missing"] == ["m4"])
    check("bad_reassign detects not-a-real-slug", res["bad_reassign"] == ["m3"])
    check("no unknown when all verdicts in targets", res["unknown"] == [])

    verdicts["m9"] = {"mention_id": "m9", "verdict": "keep", "reassign_slug": "", "evidence_quote": "", "confidence": "high"}
    res2 = validate(verdicts, target_ids, valid)
    check("unknown detects m9 not in targets", res2["unknown"] == ["m9"])

    # --- Task 5: apply_to_mentions ---
    mentions = [
        {"mention_id": "m1", "professor_slug": "john-rachlin", "professor_name": "John Rachlin", "name_key": "john rachlin", "method": "lastname", "status": "resolved"},
        {"mention_id": "m2", "professor_slug": "rongbing-liu", "professor_name": "Rongbing Liu", "name_key": "rongbing liu", "method": "lastname", "status": "resolved"},
        {"mention_id": "m4", "professor_slug": "patricia-mabrouk", "professor_name": "Patricia Mabrouk", "name_key": "patricia mabrouk", "method": "conv_context", "status": "resolved"},
    ]
    verdicts5 = {
        "m1": {"verdict": "keep", "reassign_slug": "", "evidence_quote": "rachlin is great", "confidence": "high"},
        "m2": {"verdict": "reassign", "reassign_slug": "weiling-liu", "evidence_quote": "means weiling", "confidence": "medium"},
        "m4": {"verdict": "drop", "reassign_slug": "", "evidence_quote": "thread is about housing", "confidence": "high"},
    }
    slug_info = {"weiling-liu": ("Weiling Liu", "weiling liu")}
    rows, counts = apply_to_mentions(mentions, verdicts5, slug_info)
    check("counts kept/dropped/reassigned", counts == {"kept": 1, "dropped": 1, "reassigned": 1, "skipped": 0})
    by_id = {r["mention_id"]: r for r in rows}
    check("dropped row excluded", "m4" not in by_id)
    check("reassigned repoints slug", by_id["m2"]["professor_slug"] == "weiling-liu")
    check("reassigned records original", by_id["m2"]["verify_orig_slug"] == "rongbing-liu")
    check("reassigned method tagged", by_id["m2"]["method"] == "reassigned")
    check("kept carries quote", by_id["m1"]["verify_quote"] == "rachlin is great")

    # --- Task 6: keep_keys_from + carry_forward_scores ---
    verified = [
        {"source_type": "comment", "source_id": "c1", "professor_slug": "john-rachlin", "verify_verdict": "keep"},
        {"source_type": "comment", "source_id": "c2", "professor_slug": "weiling-liu", "verify_verdict": "reassign"},
    ]
    keep_keys = keep_keys_from(verified)
    check("keep key present", ("comment", "c1", "john-rachlin") in keep_keys)
    check("reassign key NOT a keep key", ("comment", "c2", "weiling-liu") not in keep_keys)

    score_rows = [
        {"source_type": "comment", "source_id": "c1", "professor_slug": "john-rachlin", "sentiment": "positive", "score": "0.6"},
        {"source_type": "comment", "source_id": "c9", "professor_slug": "patricia-mabrouk", "sentiment": "neutral", "score": "0.0"},
    ]
    survivors, discarded = carry_forward_scores(score_rows, keep_keys)
    check("kept score survives", any(r["source_id"] == "c1" for r in survivors))
    check("non-kept score discarded", all(r["source_id"] != "c9" for r in survivors))
    check("discarded count", discarded == 1)

    # --- Task 7: run() integration test ---
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write reddit_mentions.csv (two resolved rows)
        mentions_path = os.path.join(tmpdir, "reddit_mentions.csv")
        with open(mentions_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["mention_id", "professor_slug", "professor_name",
                                               "name_key", "method", "status",
                                               "source_type", "source_id", "confidence",
                                               "thread_id", "matched_token"])
            w.writeheader()
            w.writerow({"mention_id": "r1", "professor_slug": "alice-smith",
                        "professor_name": "Alice Smith", "name_key": "alice smith",
                        "method": "lastname", "status": "resolved",
                        "source_type": "comment", "source_id": "cx1",
                        "confidence": "0.9", "thread_id": "t1", "matched_token": "smith"})
            w.writerow({"mention_id": "r2", "professor_slug": "bob-jones",
                        "professor_name": "Bob Jones", "name_key": "bob jones",
                        "method": "lastname", "status": "resolved",
                        "source_type": "comment", "source_id": "cx2",
                        "confidence": "0.9", "thread_id": "t2", "matched_token": "jones"})

        # Write verify_targets.csv (both mentions are targets)
        targets_path = os.path.join(tmpdir, "verify_targets.csv")
        with open(targets_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["mention_id", "batch", "professor_slug", "method"])
            w.writeheader()
            w.writerow({"mention_id": "r1", "batch": "1", "professor_slug": "alice-smith", "method": "lastname"})
            w.writerow({"mention_id": "r2", "batch": "1", "professor_slug": "bob-jones", "method": "lastname"})

        # Write verify_verdicts.csv: r1=keep, r2=drop
        verdicts_path = os.path.join(tmpdir, "verify_verdicts.csv")
        with open(verdicts_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["mention_id", "verdict", "reassign_slug", "evidence_quote", "confidence"])
            w.writeheader()
            w.writerow({"mention_id": "r1", "verdict": "keep", "reassign_slug": "",
                        "evidence_quote": "alice was great", "confidence": "high"})
            w.writerow({"mention_id": "r2", "verdict": "drop", "reassign_slug": "",
                        "evidence_quote": "off topic", "confidence": "high"})

        # Write sentiment_scores.csv with a score for each mention
        scores_path = os.path.join(tmpdir, "sentiment_scores.csv")
        with open(scores_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["source_type", "source_id", "professor_slug",
                                               "sentiment", "score", "on_topic",
                                               "sarcasm", "hyperbole", "rationale"])
            w.writeheader()
            w.writerow({"source_type": "comment", "source_id": "cx1", "professor_slug": "alice-smith",
                        "sentiment": "positive", "score": "0.7", "on_topic": "true",
                        "sarcasm": "false", "hyperbole": "false", "rationale": "praise"})
            w.writerow({"source_type": "comment", "source_id": "cx2", "professor_slug": "bob-jones",
                        "sentiment": "neutral", "score": "0.0", "on_topic": "false",
                        "sarcasm": "false", "hyperbole": "false", "rationale": "off topic"})

        # Call run() directly with injectable params — no real catalog needed
        rc = run(
            data_dir=tmpdir,
            valid_slugs={"alice-smith", "bob-jones"},
            slug_info={
                "alice-smith": ("Alice Smith", "alice smith"),
                "bob-jones": ("Bob Jones", "bob jones"),
            },
        )
        check("integration: run() returns 0", rc == 0)

        # Read back reddit_mentions.verified.csv produced by run()
        out_path = os.path.join(tmpdir, "reddit_mentions.verified.csv")
        with open(out_path, encoding="utf-8") as f:
            out_rows = list(csv.DictReader(f))
        check("integration: verified.csv has 1 surviving row", len(out_rows) == 1)
        check("integration: surviving row is r1 (kept)", out_rows[0]["mention_id"] == "r1")
        check("integration: audit col present", "verify_verdict" in out_rows[0])
        check("integration: dropped r2 absent from verified", all(r["mention_id"] != "r2" for r in out_rows))

        # Read back sentiment_scores.csv produced by run()
        with open(scores_path, encoding="utf-8") as f:
            final_scores = list(csv.DictReader(f))
        check("integration: score file has exactly 1 row after apply", len(final_scores) == 1)
        check("integration: score file does NOT contain bob-jones", all(r["professor_slug"] != "bob-jones" for r in final_scores))
        check("integration: surviving score is for alice-smith", final_scores[0]["professor_slug"] == "alice-smith")

    print("ALL PASS" if not fails else f"{len(fails)} FAIL")
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Apply verdicts to Reddit mentions corpus.")
    p.add_argument("--selftest", action="store_true", help="Run offline checks and exit")
    args = p.parse_args()

    if args.selftest:
        sys.exit(selftest())
    sys.exit(run())


if __name__ == "__main__":
    main()
