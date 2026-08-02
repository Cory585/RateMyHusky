"""Read-only verification gates for the ApplyWeb TRACE scores fix pipeline.

Gate 1 (offline): parse_xls.run_fixture_gate against the 4 known-good fixtures.
Gate 2: signature rate -- rows where the printed answer counts exceed
        `completed` (a hallmark of the ApplyWeb corruption). Aggregate + per-term.
        Also reconciles count_na (spec 4.4): overall rate of rows where
        counts+count_na exceed `completed` (term_id < 900 only; count_na doesn't
        exist until ingest_xls's migrate() has run).
Gate 3: avg TRACE rows/section per term (a healthy section has ~20 questions:
        19 Likert + the hours-per-week row from the All Responses sheet).
Gate 4: fixture-section reconciliation -- the 4 known sections' prod rows must
        match parse_xls.FIXTURE_META's known-good expected values (Gate 1 already
        pins those constants against the actual XLS bytes; Gate 4 only needs the
        constants + the DB, not the files themselves).
Distribution (informational only, not gated): before/after mean-bucket histogram
        split applyweb (term_id < 900) vs bluera.

`--pre` is baseline mode: dirty numbers are expected, gates 2-4 are labeled
BASELINE instead of PASS/FAIL, and the process always exits 0. Post mode
(default) exits 1 if the overall signature rate is > 0.02, the count_na
reconciliation rate is > 0.02, any main term (>= 100 sections) has avg
rows/section < 18.5, Gate 1 fails, or Gate 4 finds mismatches. Small terms
(< 100 sections) are reported but do not gate. Each DB gate is isolated: a
query error prints "ERROR: Gate N -- <message>" (never a traceback) and is
treated like a failed gate in post mode, but never in --pre. Gate 4 is
SKIPPED (not a false PASS) if the local fixtures dir isn't present.

Usage:
  python scraper/applyweb_pipeline/verify.py [--pre] [--skip-db]
  python scraper/applyweb_pipeline/verify.py --selftest
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from parse_xls import FIXTURE_META, run_fixture_gate

SIGNATURE_SQL = """
    SELECT term_id, count(*) AS n,
           count(*) FILTER (WHERE COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)
                                  +COALESCE(count_4,0)+COALESCE(count_5,0) > completed
                              AND completed > 0) AS sig
    FROM trace_scores WHERE term_id < 900 GROUP BY term_id ORDER BY term_id"""
RECONCILE_SQL = """
    SELECT count(*) AS n,
           count(*) FILTER (WHERE COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)
                                  +COALESCE(count_4,0)+COALESCE(count_5,0)+COALESCE(count_na,0) > completed
                              AND completed > 0) AS bad
    FROM trace_scores WHERE term_id < 900"""
ROWS_PER_SECTION_SQL = """
    SELECT term_id, avg(nrows) FROM (
        SELECT term_id, course_id, instructor_id, count(*) AS nrows
        FROM trace_scores WHERE term_id < 900 GROUP BY 1, 2, 3
    ) sec GROUP BY term_id ORDER BY term_id"""
FIXTURE_SECTION_SQL = """
    SELECT question, count_5, count_4, count_3, count_2, count_1, count_na, mean
    FROM trace_scores WHERE course_id = %s AND instructor_id = %s AND term_id = %s"""
DISTRIBUTION_SQL = """
    SELECT CASE WHEN term_id < 900 THEN 'applyweb' ELSE 'bluera' END AS era,
           floor(mean * 2) / 2 AS bucket, count(*)
    FROM trace_scores
    WHERE question IN ('Overall rating of teaching',
                       'What is your overall rating of this instructor teaching effectiveness?')
      AND mean IS NOT NULL
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


def gate_reconcile(conn):
    """-> (rate, n, bad) -- spec 4.4: sum(counts)+count_na <= completed should hold for
    ~all rows. Overall rate only (term_id < 900). count_na doesn't exist until
    ingest_xls's migrate() has run, so this errors on an unmigrated DB the same way
    Gate 4's FIXTURE_SECTION_SQL does."""
    with conn.cursor() as cur:
        cur.execute(RECONCILE_SQL)
        n, bad = cur.fetchall()[0]
    n, bad = n or 0, bad or 0
    return (bad / n) if n else 0.0, n, bad


def gate_rows_per_section(conn):
    """-> [(term_id, avg_rows_per_section), ...]"""
    with conn.cursor() as cur:
        cur.execute(ROWS_PER_SECTION_SQL)
        rows = cur.fetchall()
    return [(term_id, float(avg)) for term_id, avg in rows]


def gate_fixture_sections(conn, fixtures_dir=None):
    """-> list of human-readable mismatch strings; [] means every fixture section's DB
    rows match FIXTURE_META's known-good expected values. Only runs when the fixtures
    dir is present locally (used as the "this checkout has real fixtures" signal, same
    as Gate 1) -- returns [] without querying the DB if it's missing. Callers that need
    to tell that SKIP apart from a genuine pass should check os.path.isdir(fixtures_dir
    or common.FIXTURES_DIR) themselves (see run_db_gates)."""
    fixtures_dir = fixtures_dir or common.FIXTURES_DIR
    if not os.path.isdir(fixtures_dir):
        return []
    mismatches = []
    for fname, meta in FIXTURE_META.items():
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


def run_db_gates(conn, pre, fixtures_dir=None):
    """Runs gates 2-4 + the informational distribution query against conn, printing each
    gate's output as soon as it's computed and isolating each gate's DB call in its own
    try/except -- one gate's SQL error (e.g. Gate 4's FIXTURE_SECTION_SQL selects
    count_na, which doesn't exist until ingest_xls's migrate() has run, so it WILL error
    on a --pre run against an unmigrated DB) must not abort the others or lose their
    already-computed results. -> True if post mode (pre=False) should fail the run."""
    fixtures_dir = fixtures_dir or common.FIXTURES_DIR
    failed = False

    print("\n=== Gate 2: signature rate (per term) ===")
    sig_per_term = []
    try:
        sig_rate, sig_per_term = gate_signature(conn)
    except Exception as e:
        conn.rollback()   # a failed statement aborts the txn; unpoison for the next gate
        print(f"ERROR: Gate 2 -- {e}")
        if not pre:
            failed = True
    else:
        print(f"{'term_id':>10} | {'n':>8} | {'sig':>8} | {'rate':>7} | verdict")
        for term_id, n, sig, rate in sig_per_term:
            print(f"{term_id:>10} | {n:>8} | {sig:>8} | {rate:>7.4f} | {classify_signature(rate, pre)}")
        sig_verdict = classify_signature(sig_rate, pre)
        print(f"overall: n={sum(n for _, n, _, _ in sig_per_term)} rate={sig_rate:.4f} -> {sig_verdict}")
        if sig_verdict == "FAIL":
            failed = True

    try:
        reconcile_rate, reconcile_n, reconcile_bad = gate_reconcile(conn)
    except Exception as e:
        conn.rollback()
        print(f"ERROR: Gate 2 (reconcile) -- {e}")
        if not pre:
            failed = True
    else:
        reconcile_verdict = classify_signature(reconcile_rate, pre)
        print(f"reconcile: n={reconcile_n} bad={reconcile_bad} rate={reconcile_rate:.4f} -> {reconcile_verdict}")
        if reconcile_verdict == "FAIL":
            failed = True

    print("\n=== Gate 3: avg rows/section (per term) ===")
    try:
        rows_per_term = gate_rows_per_section(conn)
    except Exception as e:
        conn.rollback()
        print(f"ERROR: Gate 3 -- {e}")
        if not pre:
            failed = True
    else:
        sig_n_by_term = {term_id: n for term_id, n, _, _ in sig_per_term}
        print(f"{'term_id':>10} | {'sections':>8} | {'avg_rows':>8} | verdict")
        for term_id, avg in rows_per_term:
            n = sig_n_by_term.get(term_id, 0)
            n_sections = round(n / avg) if avg else 0
            is_main = n_sections >= MAIN_TERM_MIN_SECTIONS
            verdict = classify_rows(avg, pre)
            if verdict == "FAIL" and is_main:
                failed = True
            tag = verdict if (pre or is_main) else f"{verdict} (small term, not gated)"
            print(f"{term_id:>10} | {n_sections:>8} | {avg:>8.2f} | {tag}")

    print("\n=== Gate 4: fixture-section reconciliation ===")
    if not os.path.isdir(fixtures_dir):
        print("SKIP: fixtures not present locally (data/ is gitignored -- expected on a fresh clone)")
    else:
        try:
            fixture_mismatches = gate_fixture_sections(conn, fixtures_dir=fixtures_dir)
        except Exception as e:
            conn.rollback()
            print(f"ERROR: Gate 4 -- {e}")
            if not pre:
                failed = True
        else:
            for m in fixture_mismatches:
                print(f"  MISMATCH: {m}")
            fixture_verdict = "BASELINE" if pre else ("FAIL" if fixture_mismatches else "PASS")
            print(f"-> {fixture_verdict} ({len(fixture_mismatches)} mismatch(es))")
            if fixture_mismatches and not pre:
                failed = True

    print("\n=== Distribution (informational, not gated): mean bucket by era ===")
    try:
        distribution = gate_distribution(conn)
    except Exception as e:
        conn.rollback()
        print(f"ERROR: Distribution -- {e}")
    else:
        for era, bucket, count in distribution:
            print(f"  {era:>8} | bucket={bucket} | count={count}")

    return failed


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
        db_failed = run_db_gates(conn, args.pre)
    finally:
        conn.close()

    failed = (gate1 == "FAIL") or db_failed
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
        def rollback(self):
            pass

    class _PoisonConn:
        """Models real psycopg2 transaction poisoning: once any execute raises, every
        subsequent execute raises "current transaction is aborted" until rollback()."""
        def __init__(self, handler):
            self.handler = handler
            self.poisoned = False
        def cursor(self):
            conn = self
            class _Cur:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def execute(self, sql, params=None):
                    if conn.poisoned:
                        raise Exception("current transaction is aborted, "
                                        "commands ignored until end of transaction block")
                    try:
                        self._rows = conn.handler(sql, params)
                    except Exception:
                        conn.poisoned = True
                        raise
                def fetchall(self): return self._rows
            return _Cur()
        def rollback(self):
            self.poisoned = False

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

    # ── gate_reconcile (I1): 5 reconciliation-bad rows of 100 -> 0.05 ──
    def _reconcile_handler(sql, params):
        if "COALESCE(count_na" in sql:
            return [(100, 5)]
        return []
    reconcile_rate, reconcile_n, reconcile_bad = gate_reconcile(_FConn(_reconcile_handler))
    check("reconcile rate", abs(reconcile_rate - 0.05) < 1e-9)
    check("reconcile n/bad passthrough", (reconcile_n, reconcile_bad) == (100, 5))
    check("reconcile post-mode classification FAIL", classify_signature(reconcile_rate, pre=False) == "FAIL")
    check("reconcile pre-mode classification BASELINE", classify_signature(reconcile_rate, pre=True) == "BASELINE")

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
    # Bluera phrases the overall-teaching question differently; the era comparison is
    # useless if the SQL only matches the ApplyWeb wording (probed live 2026-08-01).
    check("distribution SQL covers bluera question text",
          "instructor teaching effectiveness" in DISTRIBUTION_SQL)

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

    # ── run_db_gates (H1): one gate's SQL error prints "ERROR: Gate N -- ..." instead
    # of an uncaught traceback, doesn't swallow the other gates' already-computed
    # output, and only fails post mode -- never --pre. Uses Gate 4's exact real-world
    # trigger: FIXTURE_SECTION_SQL selects count_na, which doesn't exist until
    # ingest_xls.migrate() has run, so a --pre run against an unmigrated DB errors here. ──
    import io, contextlib

    def _gate4_raising_handler(sql, params):
        if "course_id = %s AND instructor_id = %s" in sql:
            raise Exception('column "count_na" does not exist')
        if "COALESCE(count_na" in sql:
            return [(100, 1)]
        if "FILTER (WHERE COALESCE" in sql:
            return [(196, 100, 1)]
        if "avg(nrows)" in sql:
            return [(196, 19.0)]
        if "floor(mean * 2)" in sql:
            return [("applyweb", 4.5, 1)]
        return []

    if os.path.isdir(common.FIXTURES_DIR):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            failed_post = run_db_gates(_FConn(_gate4_raising_handler), pre=False, fixtures_dir=common.FIXTURES_DIR)
        out = buf.getvalue()
        check("gate4 query error -> ERROR line, no traceback",
              "ERROR: Gate 4" in out and "Traceback" not in out)
        check("gate4 query error doesn't swallow gates 2/3/distribution output",
              "Gate 2: signature rate" in out and "Gate 3: avg rows/section" in out and "Distribution" in out)
        check("gate4 query error fails post mode", failed_post is True)

        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            failed_pre = run_db_gates(_FConn(_gate4_raising_handler), pre=True, fixtures_dir=common.FIXTURES_DIR)
        check("gate4 query error does not fail --pre", failed_pre is False)
    else:
        print("WARN: fixtures missing -- run_db_gates Gate-4-error checks skipped")

    # ── run_db_gates (M3): fixtures dir absent -> Gate 4 must print SKIP, never a
    # false "-> PASS (0 mismatch(es))", and must not query the DB or fail either mode ──
    def _no_gate4_query_handler(sql, params):
        if "course_id = %s AND instructor_id = %s" in sql:
            raise AssertionError("Gate 4 must not query the DB when fixtures dir is absent")
        if "COALESCE(count_na" in sql:
            return [(100, 1)]
        if "FILTER (WHERE COALESCE" in sql:
            return [(196, 100, 1)]
        if "avg(nrows)" in sql:
            return [(196, 19.0)]
        if "floor(mean * 2)" in sql:
            return []
        return []

    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        failed_skip = run_db_gates(_FConn(_no_gate4_query_handler), pre=False,
                                    fixtures_dir=os.path.join(common.PIPELINE_DIR, "no_such_dir"))
    out3 = buf3.getvalue()
    check("gate4 SKIP when fixtures absent, not a false PASS",
          "SKIP: fixtures not present" in out3 and "mismatch(es)" not in out3)
    check("gate4 SKIP does not fail post mode", failed_skip is False)

    # ── run_db_gates (I1): the reconciliation query errors on an unmigrated DB (same
    # trigger as Gate 4: count_na doesn't exist pre-migration) without breaking Gate 2's
    # signature output or gates 3/4/distribution, and only fails post mode -- never --pre.
    # fixtures_dir is absent so Gate 4 takes its SKIP path (already covered above). ──
    def _reconcile_raising_handler(sql, params):
        if "COALESCE(count_na" in sql:
            raise Exception('column "count_na" does not exist')
        if "FILTER (WHERE COALESCE" in sql:
            return [(196, 100, 1)]
        if "avg(nrows)" in sql:
            return [(196, 19.0)]
        if "floor(mean * 2)" in sql:
            return [("applyweb", 4.5, 1)]
        return []

    no_fixtures_dir = os.path.join(common.PIPELINE_DIR, "no_such_dir")

    buf4 = io.StringIO()
    with contextlib.redirect_stdout(buf4):
        failed_reconcile_post = run_db_gates(_FConn(_reconcile_raising_handler), pre=False,
                                              fixtures_dir=no_fixtures_dir)
    out4 = buf4.getvalue()
    check("reconcile query error -> ERROR line, no traceback",
          "ERROR: Gate 2 (reconcile)" in out4 and "Traceback" not in out4)
    check("reconcile query error doesn't swallow gate 2 signature / gate 3 / distribution output",
          "overall: n=" in out4 and "Gate 3: avg rows/section" in out4 and "Distribution" in out4)
    check("reconcile query error fails post mode", failed_reconcile_post is True)

    buf5 = io.StringIO()
    with contextlib.redirect_stdout(buf5):
        failed_reconcile_pre = run_db_gates(_FConn(_reconcile_raising_handler), pre=True,
                                             fixtures_dir=no_fixtures_dir)
    check("reconcile query error does not fail --pre", failed_reconcile_pre is False)

    # ── run_db_gates: same scenario on a transaction-poisoning connection (real psycopg2
    # behavior on the unmigrated prod DB) -- the reconcile failure must not cascade
    # "current transaction is aborted" into gates 3/4/distribution ──
    buf_poison = io.StringIO()
    with contextlib.redirect_stdout(buf_poison):
        run_db_gates(_PoisonConn(_reconcile_raising_handler), pre=True, fixtures_dir=no_fixtures_dir)
    out_poison = buf_poison.getvalue()
    check("poisoned txn rolled back: gate 3 still computes",
          "19.00" in out_poison and "ERROR: Gate 3" not in out_poison)
    check("poisoned txn rolled back: distribution still computes",
          "applyweb" in out_poison and "transaction is aborted" not in out_poison)

    # ── run_db_gates (I1): post-mode FAIL/PASS classification off the reconcile rate,
    # isolated from the (clean) signature rate in the same run ──
    def _reconcile_rate_handler(bad_n):
        def handler(sql, params):
            if "COALESCE(count_na" in sql:
                return [(100, bad_n)]
            if "FILTER (WHERE COALESCE" in sql:
                return [(196, 100, 1)]
            if "avg(nrows)" in sql:
                return [(196, 19.0)]
            if "floor(mean * 2)" in sql:
                return [("applyweb", 4.5, 1)]
            return []
        return handler

    buf6 = io.StringIO()
    with contextlib.redirect_stdout(buf6):
        failed_reconcile_bad = run_db_gates(_FConn(_reconcile_rate_handler(5)), pre=False,
                                             fixtures_dir=no_fixtures_dir)
    out6 = buf6.getvalue()
    check("reconcile rate 0.05 -> FAIL", "reconcile: n=100 bad=5 rate=0.0500 -> FAIL" in out6)
    check("reconcile rate 0.05 fails post mode", failed_reconcile_bad is True)

    buf7 = io.StringIO()
    with contextlib.redirect_stdout(buf7):
        failed_reconcile_clean = run_db_gates(_FConn(_reconcile_rate_handler(1)), pre=False,
                                               fixtures_dir=no_fixtures_dir)
    out7 = buf7.getvalue()
    check("reconcile rate 0.01 -> PASS", "reconcile: n=100 bad=1 rate=0.0100 -> PASS" in out7)
    check("reconcile rate 0.01 passes post mode", failed_reconcile_clean is False)

    buf8 = io.StringIO()
    with contextlib.redirect_stdout(buf8):
        failed_reconcile_pre_bad = run_db_gates(_FConn(_reconcile_rate_handler(5)), pre=True,
                                                 fixtures_dir=no_fixtures_dir)
    out8 = buf8.getvalue()
    check("reconcile rate 0.05 in --pre -> BASELINE", "reconcile: n=100 bad=5 rate=0.0500 -> BASELINE" in out8)
    check("reconcile rate 0.05 in --pre does not fail", failed_reconcile_pre_bad is False)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()
