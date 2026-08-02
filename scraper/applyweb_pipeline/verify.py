"""Read-only verification gates for the ApplyWeb TRACE scores fix pipeline.

Gate 1 (offline): parse_xls.run_fixture_gate against the 4 known-good fixtures.
Gate 2: signature rate -- rows where the printed answer counts exceed
        `completed` (a hallmark of the ApplyWeb corruption). Aggregate + per-term.
Gate 3: avg TRACE rows/section per term (a healthy section has ~19 questions).
Gate 4: fixture-section reconciliation -- the 4 known sections' prod rows must
        match what parse_xls extracts from the local fixture files.
Distribution (informational only, not gated): before/after mean-bucket histogram
        split applyweb (term_id < 900) vs bluera.

`--pre` is baseline mode: dirty numbers are expected, gates 2-4 are labeled
BASELINE instead of PASS/FAIL, and the process always exits 0. Post mode
(default) exits 1 if the overall signature rate is > 0.02, any main term
(>= 100 sections) has avg rows/section < 18.5, Gate 1 fails, or Gate 4 finds
mismatches. Small terms (< 100 sections) are reported but do not gate.

Usage:
  python scraper/applyweb_pipeline/verify.py [--pre] [--skip-db]
  python scraper/applyweb_pipeline/verify.py --selftest
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from parse_xls import FIXTURE_META, parse_report, run_fixture_gate

SIGNATURE_SQL = """
    SELECT term_id, count(*) AS n,
           count(*) FILTER (WHERE COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)
                                  +COALESCE(count_4,0)+COALESCE(count_5,0) > completed
                              AND completed > 0) AS sig
    FROM trace_scores WHERE term_id < 900 GROUP BY term_id ORDER BY term_id"""
ROWS_PER_SECTION_SQL = """
    SELECT term_id, avg(nrows) FROM (
        SELECT term_id, course_id, instructor_id, count(*) AS nrows
        FROM trace_scores WHERE term_id < 900 GROUP BY 1, 2, 3
    ) GROUP BY term_id ORDER BY term_id"""
FIXTURE_SECTION_SQL = """
    SELECT question, count_5, count_4, count_3, count_2, count_1, count_na, mean
    FROM trace_scores WHERE course_id = %s AND instructor_id = %s AND term_id = %s"""
DISTRIBUTION_SQL = """
    SELECT CASE WHEN term_id < 900 THEN 'applyweb' ELSE 'bluera' END AS era,
           floor(mean * 2) / 2 AS bucket, count(*)
    FROM trace_scores
    WHERE question = 'Overall rating of teaching' AND mean IS NOT NULL
    GROUP BY 1, 2 ORDER BY 1, 2"""

SIGNATURE_RATE_MAX = 0.02
ROWS_PER_SECTION_MIN = 18.5
MAIN_TERM_MIN_SECTIONS = 100


def classify_signature(rate, pre=False):
    if pre:
        return "BASELINE"
    return "PASS" if rate <= SIGNATURE_RATE_MAX else "FAIL"


def classify_rows(avg, pre=False):
    if pre:
        return "BASELINE"
    return "PASS" if avg >= ROWS_PER_SECTION_MIN else "FAIL"


def gate_signature(conn):
    """-> (overall_rate, per_term) where per_term is [(term_id, n, sig, rate), ...]."""
    with conn.cursor() as cur:
        cur.execute(SIGNATURE_SQL)
        rows = cur.fetchall()
    per_term = []
    total_n = total_sig = 0
    for term_id, n, sig in rows:
        n, sig = n or 0, sig or 0
        per_term.append((term_id, n, sig, (sig / n) if n else 0.0))
        total_n += n
        total_sig += sig
    overall = (total_sig / total_n) if total_n else 0.0
    return overall, per_term


def gate_rows_per_section(conn):
    """-> [(term_id, avg_rows_per_section), ...]"""
    with conn.cursor() as cur:
        cur.execute(ROWS_PER_SECTION_SQL)
        rows = cur.fetchall()
    return [(term_id, float(avg)) for term_id, avg in rows]


def gate_fixture_sections(conn, fixtures_dir=None):
    """-> list of human-readable mismatch strings; [] means every fixture section's DB
    rows match what parse_xls extracts from the local fixture file. Skips entirely
    (returns []) if the fixtures dir itself is missing."""
    fixtures_dir = fixtures_dir or common.FIXTURES_DIR
    if not os.path.isdir(fixtures_dir):
        return []
    mismatches = []
    for fname, meta in FIXTURE_META.items():
        rep = parse_report(open(os.path.join(fixtures_dir, fname), "rb").read())
        expected = {row[0]: row[1:8] for row in meta["expected"]}  # question -> (c5,c4,c3,c2,c1,na,mean)
        cid, iid, tid = meta["triple"]
        with conn.cursor() as cur:
            cur.execute(FIXTURE_SECTION_SQL, (cid, iid, tid))
            db_by_q = {r[0]: r[1:] for r in cur.fetchall()}
        for q, exp in expected.items():
            got = db_by_q.get(q)
            if got is None:
                mismatches.append(f"{fname} {q!r}: missing from DB (course={cid} instructor={iid} term={tid})")
                continue
            counts_bad = got[:6] != exp[:6]  # (c5,c4,c3,c2,c1,count_na); None count_na also lands here
            mean_bad = got[6] is None or abs(got[6] - exp[6]) >= 0.005
            if counts_bad or mean_bad:
                mismatches.append(f"{fname} {q!r}: got {got} expected {exp}")
    return mismatches


def gate_distribution(conn):
    """-> [(era, bucket, count), ...] -- informational, not gated."""
    with conn.cursor() as cur:
        cur.execute(DISTRIBUTION_SQL)
        return cur.fetchall()


def run_gate1(fixtures_dir=None, verbose=True):
    """Offline fixture parse gate. -> "PASS" / "FAIL" / "SKIP". run_fixture_gate raises
    FileNotFoundError if an individual fixture file is missing (it only skips cleanly
    when the whole dir is absent), so that case is caught here rather than crashing."""
    fixtures_dir = fixtures_dir or common.FIXTURES_DIR
    if not os.path.isdir(fixtures_dir):
        print("SKIP: Gate 1 (fixture parse gate) -- fixtures dir not found")
        return "SKIP"
    try:
        ok = run_fixture_gate(fixtures_dir=fixtures_dir, verbose=verbose)
    except FileNotFoundError as e:
        print(f"FAIL: Gate 1 (fixture parse gate) -- missing fixture file: {e}")
        return "FAIL"
    return "PASS" if ok else "FAIL"


def main():
    parser = argparse.ArgumentParser(description="Verification gates for the ApplyWeb TRACE scores fix.")
    parser.add_argument("--pre", action="store_true",
                        help="Baseline mode: label DB gates BASELINE (dirty data expected), always exit 0")
    parser.add_argument("--skip-db", action="store_true", help="Run only Gate 1 (offline, no DB connection)")
    args = parser.parse_args()

    print("=== Gate 1: fixture parse gate (offline) ===")
    gate1 = run_gate1()

    if args.skip_db:
        sys.exit(0 if (args.pre or gate1 != "FAIL") else 1)

    conn = common.connect()
    try:
        sig_rate, sig_per_term = gate_signature(conn)
        rows_per_term = gate_rows_per_section(conn)
        fixture_mismatches = gate_fixture_sections(conn)
        distribution = gate_distribution(conn)
    finally:
        conn.close()

    failed = gate1 == "FAIL"

    print("\n=== Gate 2: signature rate (per term) ===")
    print(f"{'term_id':>10} | {'n':>8} | {'sig':>8} | {'rate':>7} | verdict")
    for term_id, n, sig, rate in sig_per_term:
        print(f"{term_id:>10} | {n:>8} | {sig:>8} | {rate:>7.4f} | {classify_signature(rate, args.pre)}")
    sig_verdict = classify_signature(sig_rate, args.pre)
    print(f"overall: n={sum(n for _, n, _, _ in sig_per_term)} rate={sig_rate:.4f} -> {sig_verdict}")
    if sig_verdict == "FAIL":
        failed = True

    print("\n=== Gate 3: avg rows/section (per term) ===")
    sig_n_by_term = {term_id: n for term_id, n, _, _ in sig_per_term}
    print(f"{'term_id':>10} | {'sections':>8} | {'avg_rows':>8} | verdict")
    for term_id, avg in rows_per_term:
        n = sig_n_by_term.get(term_id, 0)
        n_sections = round(n / avg) if avg else 0
        is_main = n_sections >= MAIN_TERM_MIN_SECTIONS
        verdict = classify_rows(avg, args.pre)
        if verdict == "FAIL" and is_main:
            failed = True
        tag = verdict if (args.pre or is_main) else f"{verdict} (small term, not gated)"
        print(f"{term_id:>10} | {n_sections:>8} | {avg:>8.2f} | {tag}")

    print("\n=== Gate 4: fixture-section reconciliation ===")
    if fixture_mismatches:
        for m in fixture_mismatches:
            print(f"  MISMATCH: {m}")
        if not args.pre:
            failed = True
    fixture_verdict = "BASELINE" if args.pre else ("FAIL" if fixture_mismatches else "PASS")
    print(f"-> {fixture_verdict} ({len(fixture_mismatches)} mismatch(es))")

    print("\n=== Distribution (informational, not gated): mean bucket by era ===")
    for era, bucket, count in distribution:
        print(f"  {era:>8} | bucket={bucket} | count={count}")

    if args.pre:
        print("\n--pre baseline run: exiting 0 regardless of gate results.")
        sys.exit(0)
    sys.exit(1 if failed else 0)


def selftest():
    fails = []

    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    class _FCur:
        def __init__(self, handler):
            self.handler = handler
            self._rows = []
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            self._rows = self.handler(sql, params)
        def fetchall(self):
            return self._rows

    class _FConn:
        def __init__(self, handler):
            self.handler = handler
        def cursor(self):
            return _FCur(self.handler)

    # ── classify_signature / classify_rows: pure threshold logic ──
    check("signature verdict post", classify_signature(0.9, pre=False) == "FAIL")
    check("signature verdict pre", classify_signature(0.9, pre=True) == "BASELINE")
    check("signature verdict clean", classify_signature(0.01, pre=False) == "PASS")
    check("rows-per-section verdict", classify_rows(19.0) == "PASS" and classify_rows(17.1) == "FAIL")
    check("rows-per-section pre baseline", classify_rows(17.1, pre=True) == "BASELINE")

    # ── gate_signature: 90 signature rows of 100 -> 0.9 ──
    def _sig_handler(sql, params):
        if "FILTER (WHERE COALESCE" in sql:
            return [(196, 100, 90)]
        return []
    fake_sig = _FConn(_sig_handler)
    rate, per_term = gate_signature(fake_sig)
    check("signature rate", abs(rate - 0.9) < 1e-9)
    check("signature per_term", per_term == [(196, 100, 90, 0.9)])

    # ── gate_rows_per_section: passthrough + float cast ──
    def _rows_handler(sql, params):
        if "avg(nrows)" in sql:
            return [(196, 19), (145, "17.10")]
        return []
    fake_rows = _FConn(_rows_handler)
    check("rows per section parsed", gate_rows_per_section(fake_rows) == [(196, 19.0), (145, 17.1)])

    # ── gate_distribution: straight passthrough ──
    def _dist_handler(sql, params):
        if "floor(mean * 2)" in sql:
            return [("applyweb", 4.5, 10), ("bluera", 4.5, 20)]
        return []
    check("distribution passthrough", gate_distribution(_FConn(_dist_handler)) ==
          [("applyweb", 4.5, 10), ("bluera", 4.5, 20)])

    # ── gate_fixture_sections: canned DB rows built from FIXTURE_META itself, so this
    # exercises the real local fixture files + the real FIXTURE_META in this checkout ──
    def _db_rows_for(meta, corrupt):
        rows = []
        for i, row in enumerate(meta["expected"]):
            q, c5, c4, c3, c2, c1, na, mean = row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]
            if corrupt and i == 0:
                c5 += 1
            rows.append((q, c5, c4, c3, c2, c1, na, mean))
        return rows

    def _fixture_handler(corrupt_fname):
        def handler(sql, params):
            if "course_id = %s AND instructor_id = %s" not in sql:
                return []
            triple = tuple(params)
            for fname, meta in FIXTURE_META.items():
                if meta["triple"] == triple:
                    return _db_rows_for(meta, corrupt=(fname == corrupt_fname))
            return []
        return handler

    if os.path.isdir(common.FIXTURES_DIR):
        fake_with_expected1 = _FConn(_fixture_handler(corrupt_fname=None))
        check("fixture section match", gate_fixture_sections(fake_with_expected1) == [])

        first_fname = next(iter(FIXTURE_META))
        fake_with_wrong_count = _FConn(_fixture_handler(corrupt_fname=first_fname))
        check("fixture section mismatch caught", gate_fixture_sections(fake_with_wrong_count) != [])

        # count_na None (pre-migration column) always counts as a mismatch
        def _null_na_handler(sql, params):
            if "course_id = %s AND instructor_id = %s" not in sql:
                return []
            triple = tuple(params)
            for meta in FIXTURE_META.values():
                if meta["triple"] == triple:
                    return [(row[0], row[1], row[2], row[3], row[4], row[5], None, row[7])
                            for row in meta["expected"]]
            return []
        check("count_na None flagged as mismatch", gate_fixture_sections(_FConn(_null_na_handler)) != [])
    else:
        print("WARN: fixtures missing -- gate_fixture_sections real-file checks skipped")

    # dir missing entirely -> [] (no conn calls needed, no crash)
    check("fixture gate dir-missing -> []",
          gate_fixture_sections(_FConn(lambda sql, params: (_ for _ in ()).throw(AssertionError("should not query"))),
                                 fixtures_dir=os.path.join(common.PIPELINE_DIR, "no_such_dir")) == [])

    # ── run_gate1: the known-issue wrap -- dir missing -> SKIP, dir present but a fixture
    # file missing -> FAIL (not an uncaught FileNotFoundError traceback) ──
    import tempfile, shutil
    tmp_dir = tempfile.mkdtemp()
    try:
        check("run_gate1 SKIP when dir missing",
              run_gate1(fixtures_dir=os.path.join(tmp_dir, "absent"), verbose=False) == "SKIP")
        os.makedirs(os.path.join(tmp_dir, "present"), exist_ok=True)
        try:
            result = run_gate1(fixtures_dir=os.path.join(tmp_dir, "present"), verbose=False)
            check("run_gate1 FAIL (not traceback) when a fixture file is missing", result == "FAIL")
        except FileNotFoundError:
            check("run_gate1 FAIL (not traceback) when a fixture file is missing", False)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()
