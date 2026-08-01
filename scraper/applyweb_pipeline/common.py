"""Shared helpers for the ApplyWeb TRACE scores fix pipeline: DB connect w/ DNS
retry, serialization-retry writes, cookie-file parsing.

Adapted from trace_pipeline/common.py.

Usage:  python scraper/applyweb_pipeline/common.py --selftest
"""
import os, sys, time

from dotenv import load_dotenv

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(_REPO_ROOT, "backend", ".env"))

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PIPELINE_DIR, "data")
FIXTURES_DIR = os.path.join(DATA_DIR, "fixtures")

def get_db_url():
    url = os.getenv("NEW_CRDB_DATABASE_URL")
    if not url:
        sys.exit("Missing NEW_CRDB_DATABASE_URL in backend/.env")
    return url

def connect(attempts=20):
    """DNS-retry connect: this machine's resolver flakes on *.cockroachlabs.cloud."""
    import psycopg2
    url = get_db_url()
    last = None
    for i in range(1, attempts + 1):
        try:
            return psycopg2.connect(url, sslmode="require")
        except psycopg2.OperationalError as e:
            if "could not translate host name" not in str(e):
                raise
            last = str(e)
            print(f"  DNS lookup flaked; retrying ({i}/{attempts})...")
            time.sleep(3)
    sys.exit(f"Could not resolve CRDB host after {attempts} attempts.\n{last}")

def execute_with_retry(conn, sql, params=(), attempts=6):
    import psycopg2
    for attempt in range(attempts):
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rc = cur.rowcount
            conn.commit()
            return rc
        except psycopg2.errors.SerializationFailure:
            conn.rollback()
            if attempt == attempts - 1:
                raise
            time.sleep(0.5 * (2 ** attempt))

def batched_write(conn, sql, rows, template=None, batch=1000, attempts=6):
    """Multi-row INSERT per CRDB guidance: one statement per implicit txn, commit per chunk."""
    import psycopg2
    from psycopg2.extras import execute_values
    total = 0
    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        for attempt in range(attempts):
            try:
                with conn.cursor() as cur:
                    execute_values(cur, sql, chunk, template=template, page_size=batch)
                conn.commit()
                break
            except psycopg2.errors.SerializationFailure:
                conn.rollback()
                if attempt == attempts - 1:
                    raise
                time.sleep(0.5 * (2 ** attempt))
        total += len(chunk)
    return total

def parse_cookie_header(text):
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):]
    out = {}
    for seg in text.split(";"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        k, _, v = seg.partition("=")
        out[k.strip()] = v.strip()
    return out

def load_cookies(path):
    if not os.path.exists(path) or not open(path, encoding="utf-8").read().strip():
        sys.exit(f"Cookie file missing/empty: {path}\n"
                 "Open DevTools on applyweb.com -> Network -> any request ->\n"
                 "copy the 'Cookie:' request header value into that file. NEVER commit it.")
    return parse_cookie_header(open(path, encoding="utf-8").read())

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    # ── parse_cookie_header ──
    check("cookie basic", parse_cookie_header("a=1; b=2") == {"a": "1", "b": "2"})
    check("cookie strips Cookie: prefix",
          parse_cookie_header("Cookie: a=1; b=2") == {"a": "1", "b": "2"})
    check("cookie value containing =",
          parse_cookie_header("tok=abc==; x=1") == {"tok": "abc==", "x": "1"})
    check("cookie skips empty segments", parse_cookie_header("a=1; ; b=2;") == {"a": "1", "b": "2"})

    # ── batched_write: batching + commit-per-chunk + retry ──
    calls = {"execs": [], "commits": 0, "fail_first": [True]}
    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): calls["commits"] += 1
        def rollback(self): pass
    import psycopg2.extras as _pge
    _saved = _pge.execute_values
    def _fake_ev(cur, sql, chunk, template=None, page_size=None):
        if calls["fail_first"][0]:
            calls["fail_first"][0] = False
            import psycopg2
            raise psycopg2.errors.SerializationFailure()
        calls["execs"].append(len(chunk))
    _pge.execute_values = _fake_ev
    try:
        n = batched_write(_Conn(), "INSERT INTO t (a) VALUES %s", [(i,) for i in range(25)], batch=10)
    finally:
        _pge.execute_values = _saved
    check("batched_write returns total", n == 25)
    check("batched_write chunks 10/10/5", calls["execs"] == [10, 10, 5])
    check("batched_write commits per chunk", calls["commits"] == 3)

    # ── connect: retries DNS failure then gives up ──
    import psycopg2 as _pg
    attempts = {"n": 0}
    _saved_connect = _pg.connect
    def _fake_connect(*a, **k):
        attempts["n"] += 1
        raise _pg.OperationalError("could not translate host name \"x.cockroachlabs.cloud\"")
    _pg.connect = _fake_connect
    _saved_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        try:
            connect(attempts=3)
            check("connect exits after retries", False)
        except SystemExit:
            check("connect exits after retries", True)
    finally:
        _pg.connect = _saved_connect
        time.sleep = _saved_sleep
    check("connect retried 3 times", attempts["n"] == 3)

    # ── module layout ──
    check("data dirs resolve inside package", DATA_DIR.endswith(os.path.join("applyweb_pipeline", "data")))

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("Import-only module. Use --selftest.")
