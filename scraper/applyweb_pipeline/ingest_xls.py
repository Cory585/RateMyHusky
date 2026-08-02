"""Cache -> DB ingest for ApplyWeb-era TRACE score fix pipeline.

Parses the XLS cache built by scrape_xls.py, section-scoped replaces the
corrupted rows in trace_scores (Bluera-era term_id >= 900 is refused before
any SQL runs), and dual-writes the same replacement into the flat CSV export
(upgrading its header with the new count_na column along the way).

Usage:
  python scraper/applyweb_pipeline/ingest_xls.py --dry-run
  python scraper/applyweb_pipeline/ingest_xls.py --terms 145,148
  python scraper/applyweb_pipeline/ingest_xls.py --migrate
  python scraper/applyweb_pipeline/ingest_xls.py --selftest
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from parse_xls import parse_report, report_ok

CSV_PATH = os.path.join(common._REPO_ROOT, "backend", "Better_Scraper", "output_data", "trace_scores.csv")
CSV_FIELDS = ["courseId", "instructorId", "termId", "enrollment", "completed", "question",
              "count_5", "count_4", "count_3", "count_2", "count_1",
              "mean", "median", "std_dev", "dept_mean", "count_na"]   # count_na appended LAST (new col)
DB_FIELD_ORDER = ["courseId", "instructorId", "termId", "enrollment", "completed", "question",
                  "count_5", "count_4", "count_3", "count_2", "count_1", "count_na",
                  "mean", "median", "std_dev", "dept_mean"]
INSERT_SQL = """INSERT INTO trace_scores (course_id, instructor_id, term_id, enrollment, completed, question,
    count_5, count_4, count_3, count_2, count_1, count_na, mean, median, std_dev, dept_mean)
    VALUES %s ON CONFLICT (course_id, instructor_id, term_id, question) DO NOTHING"""


def build_section_rows(rep, cid, iid, tid):
    rows = []
    for q in rep["questions"]:
        rows.append({"courseId": cid, "instructorId": iid, "termId": tid,
                     "enrollment": rep["enrollment"], "completed": rep["completed"],
                     "question": q["question"],
                     "count_5": q["count_5"], "count_4": q["count_4"], "count_3": q["count_3"],
                     "count_2": q["count_2"], "count_1": q["count_1"], "count_na": q["count_na"],
                     "mean": "" if q["mean"] is None else q["mean"],
                     "median": "" if q["median"] is None else q["median"],
                     "std_dev": "" if q["std_dev"] is None else q["std_dev"],
                     "dept_mean": ""})
    return rows


def migrate(conn):
    common.execute_with_retry(conn, "ALTER TABLE trace_scores ADD COLUMN IF NOT EXISTS count_na INT")


def replace_sections(conn, term_id, pairs, db_rows, chunk=100):
    """Section-scoped replace: delete exactly the (course,instructor) pairs being re-inserted."""
    if term_id >= 900:
        sys.exit(f"REFUSING to touch term_id {term_id} (>= 900 is Bluera-era).")
    for i in range(0, len(pairs), chunk):
        common.execute_with_retry(
            conn, "DELETE FROM trace_scores WHERE term_id = %s AND (course_id, instructor_id) IN %s",
            (term_id, tuple(pairs[i:i + chunk])))
    return common.batched_write(conn, INSERT_SQL, db_rows, batch=1000)


def delete_full_term(conn, term_id, batch=5000):
    """Whole-term purge for terms whose download coverage is confirmed complete. Must run
    BEFORE replace_sections for that term_id, never after -- an unqualified DELETE issued
    after the section-scoped INSERT would wipe the fresh rows it just wrote. Relocated out
    of replace_sections' call path, so it carries its own Bluera guard (house precedent:
    scraper/trace_pipeline/ingest.py delete_term -- LIMIT-batched, deletes before inserts)."""
    if term_id >= 900:
        sys.exit(f"REFUSING to touch term_id {term_id} (>= 900 is Bluera-era).")
    total = 0
    while True:
        n = common.execute_with_retry(
            conn, "DELETE FROM trace_scores WHERE term_id = %s LIMIT %s", (term_id, batch))
        total += max(n, 0)
        if n <= 0:
            break
    return total


def iter_cache(data_dir, terms=None):
    pat = re.compile(r"^(\d+)_(\d+)_(\d+)\.xls$")
    xls_root = os.path.join(data_dir, "xls")
    for tdir in sorted(os.listdir(xls_root)):
        tpath = os.path.join(xls_root, tdir)
        if not os.path.isdir(tpath):
            continue
        for fname in sorted(os.listdir(tpath)):
            m = pat.match(fname)
            if not m:
                continue
            cid, iid, tid = (int(g) for g in m.groups())
            if terms and tid not in terms:
                continue
            yield cid, iid, tid, os.path.join(tpath, fname)


def rewrite_scores_csv(csv_path, replaced_triples, new_rows):
    """Stream-rewrite: drop replaced (courseId,instructorId,termId) string-triples, upgrade
    header to CSV_FIELDS (adds count_na), append new rows. Caller makes the .bak first."""
    import csv as _csv
    tmp = csv_path + ".tmp"
    removed = 0
    with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as fin, \
         open(tmp, "w", encoding="utf-8", newline="") as fout:
        reader = _csv.DictReader(fin)
        writer = _csv.DictWriter(fout, fieldnames=CSV_FIELDS, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in reader:
            if (row.get("courseId"), row.get("instructorId"), row.get("termId")) in replaced_triples:
                removed += 1
                continue
            writer.writerow(row)
        for row in new_rows:
            writer.writerow(row)
    os.replace(tmp, csv_path)
    return removed, len(new_rows)


def _failure_reason(rep):
    parts = []
    if rep["enrollment"] is None or rep["completed"] is None:
        parts.append("missing enrollment/completed")
    if not rep["questions"]:
        parts.append("no questions")
    if rep["unexpected_rows"]:
        parts.append(f"unexpected_rows={rep['unexpected_rows']}")
    if rep["mean_mismatches"]:
        parts.append(f"mean_mismatches={rep['mean_mismatches']}")
    if rep["duplicate_questions"]:
        parts.append(f"duplicate_questions={rep['duplicate_questions']}")
    return "; ".join(parts) or "unknown"


def selftest():
    fails = []

    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    import tempfile, shutil

    # ── build_section_rows: camelCase keys, count_na, None -> "" for CSV friendliness ──
    rep = {"enrollment": 22, "completed": 15, "questions": [
        {"question": "Syllabus", "count_5": 14, "count_4": 0, "count_3": 0, "count_2": 0, "count_1": 0,
         "count_na": 0, "mean": 5.0, "median": 5.0, "std_dev": 0.0},
        {"question": "AllNA", "count_5": 0, "count_4": 0, "count_3": 0, "count_2": 0, "count_1": 0,
         "count_na": 15, "mean": None, "median": None, "std_dev": None}]}
    rows = build_section_rows(rep, 102980, 87, 196)
    check("row keys match CSV_FIELDS", set(rows[0]) == set(CSV_FIELDS))
    check("ids + counts mapped", rows[0]["courseId"] == 102980 and rows[0]["count_5"] == 14 and rows[0]["count_na"] == 0)
    check("None stats -> empty string", rows[1]["mean"] == "" and rows[1]["median"] == "" and rows[1]["std_dev"] == "")
    check("dept_mean always empty", rows[0]["dept_mean"] == "")

    # ── precompute-equivalence: stored mean == what precompute recomputes from counts ──
    tot = sum(rows[0][k] for k in ("count_1", "count_2", "count_3", "count_4", "count_5"))
    wsum = sum(i * rows[0][f"count_{i}"] for i in range(1, 6))
    check("precompute recompute converges", abs(rows[0]["mean"] - wsum / tot) < 0.005)

    # ── SQL-capture fakes (house pattern from trace_pipeline/ingest.py selftest) ──
    class _FCur:
        def __init__(self, log):
            self.log = log
            self.rowcount = 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            self.log.append(re.sub(r"\s+", " ", sql).strip())

    class _FakeConn:
        def __init__(self):
            self.sqls = []
        def cursor(self):
            return _FCur(self.sqls)
        def commit(self): pass
        def rollback(self): pass

    # ── term guard: term_id >= 900 must abort BEFORE any SQL (zero SQL captured, not just
    # SystemExit -- a guard moved after the DELETE loop would still raise but leak SQL) ──
    import psycopg2.extras as _pge
    _saved_ev = _pge.execute_values
    guard_ev_calls = []
    _pge.execute_values = lambda *a, **k: guard_ev_calls.append(1)
    try:
        guard_fake = _FakeConn()
        try:
            replace_sections(guard_fake, 901, [(1, 2)], [])
            check("bluera term guard", False)
        except SystemExit:
            check("bluera term guard", True)
        check("bluera term guard issues zero SQL", guard_fake.sqls == [] and guard_ev_calls == [])

        guard_fake2 = _FakeConn()
        try:
            delete_full_term(guard_fake2, 901)
            check("delete_full_term bluera guard", False)
        except SystemExit:
            check("delete_full_term bluera guard", True)
        check("delete_full_term guard issues zero SQL", guard_fake2.sqls == [] and guard_ev_calls == [])
    finally:
        _pge.execute_values = _saved_ev

    # ── replace_sections: section-scoped DELETE before INSERT, chunked ──
    rows_as_db_tuples = [tuple(r[f] if r[f] != "" else None for f in DB_FIELD_ORDER) for r in rows]
    fake = _FakeConn()
    _saved_ev = _pge.execute_values

    def _fake_ev(cur, sql, chunk, template=None, page_size=None):
        fake.sqls.append(re.sub(r"\s+", " ", sql).strip())
    _pge.execute_values = _fake_ev
    try:
        replace_sections(fake, 196, [(102980, 87)], rows_as_db_tuples)
    finally:
        _pge.execute_values = _saved_ev
    check("delete is section-scoped",
          "DELETE FROM trace_scores WHERE term_id = %s AND (course_id, instructor_id) IN %s" in fake.sqls[0])
    delete_idx = next(i for i, s in enumerate(fake.sqls) if s.startswith("DELETE"))
    insert_idx = next(i for i, s in enumerate(fake.sqls) if s.startswith("INSERT"))
    check("delete precedes insert", delete_idx < insert_idx)
    check("insert has count_na column", "count_na" in fake.sqls[insert_idx])

    # ── rewrite_scores_csv: drops replaced triples, upgrades header, appends new rows, restval "" ──
    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_csv = os.path.join(tmp_dir, "trace_scores.csv")
        old_fields = CSV_FIELDS[:-1]  # old 15-col header, no count_na
        with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=old_fields)
            w.writeheader()
            w.writerow({**{k: "" for k in old_fields}, "courseId": "102980", "instructorId": "87",
                        "termId": "196", "question": "Old Q"})
            w.writerow({**{k: "" for k in old_fields}, "courseId": "1", "instructorId": "2",
                        "termId": "145", "question": "Keep 1"})
            w.writerow({**{k: "" for k in old_fields}, "courseId": "3", "instructorId": "4",
                        "termId": "148", "question": "Keep 2"})
        removed, appended = rewrite_scores_csv(tmp_csv, {("102980", "87", "196")}, rows)
        check("replaced rows dropped", removed == 1)
        check("new rows appended", appended == 2)
        with open(tmp_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            reread_fieldnames = reader.fieldnames
            reread_rows = list(reader)
        check("header upgraded", reread_fieldnames == CSV_FIELDS)
        untouched_row = next(r for r in reread_rows if r["question"] == "Keep 1")
        check("untouched rows keep data, blank count_na", untouched_row["count_na"] == "")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── iter_cache: filename regex + term filter ──
    tmp_dir2 = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp_dir2, "xls", "196"), exist_ok=True)
        os.makedirs(os.path.join(tmp_dir2, "xls", "145"), exist_ok=True)
        open(os.path.join(tmp_dir2, "xls", "196", "102980_87_196.xls"), "wb").close()
        open(os.path.join(tmp_dir2, "xls", "145", "1_2_145.xls"), "wb").close()
        open(os.path.join(tmp_dir2, "xls", "196", "junk.txt"), "wb").close()
        open(os.path.join(tmp_dir2, "xls", "196", "5_6_196.xls.tmp"), "wb").close()  # interrupted download
        open(os.path.join(tmp_dir2, "xls", "failures.csv"), "wb").close()  # cache-root file, not a term dir
        found_triples = {(cid, iid, tid) for cid, iid, tid, _ in iter_cache(tmp_dir2)}
        check("iter_cache finds triples", found_triples == {(102980, 87, 196), (1, 2, 145)})
        triples_with_terms_196 = [(cid, iid, tid) for cid, iid, tid, _ in iter_cache(tmp_dir2, terms={196})]
        check("iter_cache term filter", triples_with_terms_196 == [(102980, 87, 196)])
    finally:
        shutil.rmtree(tmp_dir2, ignore_errors=True)

    # ── migrate: emits idempotent ALTER ──
    fake2 = _FakeConn()
    migrate(fake2)
    check("migrate adds count_na", "ADD COLUMN IF NOT EXISTS count_na INT" in fake2.sqls[0])

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


def main():
    parser = argparse.ArgumentParser(description="Ingest cached ApplyWeb XLS reports into trace_scores.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse the cache and print a summary; no DB or CSV writes")
    parser.add_argument("--terms", help="Comma-separated term_id filter, e.g. 145,148")
    parser.add_argument("--migrate", action="store_true", help="Run the count_na ALTER TABLE standalone and exit")
    parser.add_argument("--delete-full-term", action="store_true",
                        help="Before each term's section-scoped replace, purge ALL rows for "
                             "that term_id (LIMIT-batched) -- only for terms whose download "
                             "coverage is confirmed complete")
    parser.add_argument("--selftest", action="store_true", help="Run offline selftest and exit")
    args = parser.parse_args()

    if args.migrate:
        conn = common.connect()
        try:
            migrate(conn)
        finally:
            conn.close()
        print("Migration applied: count_na column present on trace_scores.")
        return

    terms = {int(t) for t in args.terms.split(",")} if args.terms else None

    # 1. Parse the whole cache; quarantine non-ok reports.
    failures = []
    by_term = {}
    dirty_by_term = {}
    clean, dirty = 0, 0
    for cid, iid, tid, path in iter_cache(common.DATA_DIR, terms=terms):
        try:
            with open(path, "rb") as f:
                rep = parse_report(f.read())
        except Exception as e:
            failures.append((cid, iid, tid, repr(e)))
            dirty_by_term[tid] = dirty_by_term.get(tid, 0) + 1
            dirty += 1
            continue
        if not report_ok(rep):
            failures.append((cid, iid, tid, _failure_reason(rep)))
            dirty_by_term[tid] = dirty_by_term.get(tid, 0) + 1
            dirty += 1
            continue
        clean += 1
        by_term.setdefault(tid, []).append((cid, iid, rep))

    if failures:
        fail_path = os.path.join(common.DATA_DIR, "xls", "parse_failures.csv")
        os.makedirs(os.path.dirname(fail_path), exist_ok=True)
        with open(fail_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["course_id", "instructor_id", "term_id", "reason"])
            w.writerows(failures)

    # 2. Per-term summary: build rows for clean sections while we're at it.
    all_rows_by_term = {}
    print(f"{'term_id':>10} | clean | dirty | rows | rows/section")
    for tid in sorted(set(by_term) | set(dirty_by_term)):
        sections = by_term.get(tid, [])
        term_rows = []
        for cid, iid, rep in sections:
            term_rows.extend(build_section_rows(rep, cid, iid, tid))
        if sections:
            all_rows_by_term[tid] = term_rows
        avg = len(term_rows) / len(sections) if sections else 0
        print(f"{tid:>10} | {len(sections):>5} | {dirty_by_term.get(tid, 0):>5} | {len(term_rows):>4} | {avg:>6.1f}")
    print(f"total: {clean} clean section(s), {dirty} dirty (quarantined to parse_failures.csv)")

    if args.dry_run:
        print("Dry run: no DB or CSV writes performed.")
        return

    # 3. Live mode: migrate, then per-term (ascending) section-scoped replace.
    conn = common.connect()
    replaced_triples = set()
    all_new_rows = []
    try:
        migrate(conn)
        for tid in sorted(all_rows_by_term):
            sections = by_term[tid]
            rows = all_rows_by_term[tid]
            pairs = sorted({(cid, iid) for cid, iid, _ in sections})
            db_rows = [tuple(r[f] if r[f] != "" else None for f in DB_FIELD_ORDER) for r in rows]
            if args.delete_full_term:
                total = delete_full_term(conn, tid)
                print(f"  term {tid}: --delete-full-term purged {total} row(s) before replace")
            n_inserted = replace_sections(conn, tid, pairs, db_rows)
            print(f"  term {tid}: deleted {len(pairs)} section(s), inserted {n_inserted} row(s)")
            for cid, iid, _ in sections:
                replaced_triples.add((str(cid), str(iid), str(tid)))
            all_new_rows.extend(rows)
    finally:
        conn.close()

    # 4. CSV dual-write: safety copy, then stream-rewrite.
    import shutil, datetime
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    if os.path.exists(CSV_PATH):
        shutil.copy2(CSV_PATH, CSV_PATH + f".bak-{stamp}")
    removed, appended = rewrite_scores_csv(CSV_PATH, replaced_triples, all_new_rows)
    print(f"CSV: dropped {removed} replaced row(s), appended {appended} new row(s)")

    # 5. Final summary.
    print("Now run: python backend/backup_db.py && python backend/precompute.py  (see README)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()
