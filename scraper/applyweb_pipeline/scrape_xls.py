"""Resumable downloader for ApplyWeb-era TRACE quantitative XLS reports.

Cache layout `data/xls/{tid}/{cid}_{iid}_{tid}.xls` (existing valid files are
skipped, so re-running resumes for free). A 401 halts the whole run (cookie
expired) rather than burning through the remaining queue.

Usage:
  python scraper/applyweb_pipeline/scrape_xls.py --dry-run
  python scraper/applyweb_pipeline/scrape_xls.py --cookie "..." [--terms 145,148] [--limit N]
  python scraper/applyweb_pipeline/scrape_xls.py --selftest
"""
import argparse
import csv
import os
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

XLS_URL = ("https://www.applyweb.com/eval/EvalGatekeeper/EvalGatekeeper"
           "?service=QuantitativeXls&sp={cid}&sp={iid}&sp={tid}")
OLE_MAGIC = b"\xd0\xcf\x11\xe0"   # legacy .xls compound-file magic; login-page HTML fails this
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
           "Referer": "https://www.applyweb.com/eval/new/reportbrowser"}
TARGET_SQL = """SELECT DISTINCT course_id, instructor_id, term_id
                FROM trace_courses
                WHERE term_id < 900 AND term_end_date >= '2021-01-01'
                ORDER BY term_id, course_id, instructor_id"""


def build_url(cid, iid, tid):
    return XLS_URL.format(cid=cid, iid=iid, tid=tid)


def xls_path(data_dir, cid, iid, tid):
    return os.path.join(data_dir, "xls", str(tid), f"{cid}_{iid}_{tid}.xls")


def validate_xls_bytes(b):
    return b is not None and len(b) >= 512 and b[:4] == OLE_MAGIC


def download_one(session, triple, data_dir, halt, retries=2, timeout=60):
    """One section XLS -> cache. Returns ok|skip|fail|auth|halted. Never raises."""
    if halt.is_set():
        return "halted"
    cid, iid, tid = triple
    path = xls_path(data_dir, cid, iid, tid)
    if os.path.exists(path):
        with open(path, "rb") as f:
            if validate_xls_bytes(f.read()):
                return "skip"
    for attempt in range(retries + 1):
        try:
            resp = session.get(build_url(cid, iid, tid), timeout=timeout)
        except Exception:
            continue
        if resp.status_code == 401:
            halt.set()          # cookie expired -> stop the whole run (resume is free)
            return "auth"
        if resp.status_code == 200 and validate_xls_bytes(resp.content):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(resp.content)
            os.replace(tmp, path)
            return "ok"
    return "fail"


def fetch_targets(conn):
    with conn.cursor() as cur:
        cur.execute(TARGET_SQL)
        return cur.fetchall()


def selftest():
    fails = []

    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    check("url formation", build_url(102980, 87, 196).endswith("sp=102980&sp=87&sp=196"))
    check("xls path layout", xls_path("D", 1, 2, 3).replace(os.sep, "/") == "D/xls/3/1_2_3.xls")
    check("magic accepted", validate_xls_bytes(OLE_MAGIC + b"\x00" * 600))
    check("html rejected", not validate_xls_bytes(b"<html>login</html>" + b" " * 600))
    check("short body rejected", not validate_xls_bytes(OLE_MAGIC + b"\x00" * 10))

    class _Resp:
        def __init__(self, status, content=b""): self.status_code, self.content = status, content

    class _Sess:
        def __init__(self, script): self.script = list(script)
        def get(self, url, timeout=None): return self.script.pop(0)

    import threading, tempfile
    with tempfile.TemporaryDirectory() as td:
        halt = threading.Event()
        good = OLE_MAGIC + b"\x00" * 600
        check("ok download writes file",
              download_one(_Sess([_Resp(200, good)]), (1, 2, 3), td, halt) == "ok"
              and open(xls_path(td, 1, 2, 3), "rb").read() == good)
        check("existing valid file skipped",
              download_one(_Sess([]), (1, 2, 3), td, halt) == "skip")   # no request made
        check("retry then fail on bad content",
              download_one(_Sess([_Resp(200, b"<html>"), _Resp(200, b"<html>"), _Resp(200, b"<html>")]),
                           (4, 5, 6), td, halt) == "fail")
        check("401 sets halt", download_one(_Sess([_Resp(401)]), (7, 8, 9), td, halt) == "auth" and halt.is_set())
        check("halted short-circuits", download_one(_Sess([]), (10, 11, 12), td, halt) == "halted")

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


def main():
    parser = argparse.ArgumentParser(description="Download ApplyWeb-era TRACE quantitative XLS reports.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List per-term target counts and exit; downloads nothing")
    parser.add_argument("--cookie", help='Raw "Cookie:" header string (else cookies.txt next to this script)')
    parser.add_argument("--terms", help="Comma-separated term_id filter, e.g. 145,148")
    parser.add_argument("--limit", type=int, help="Limit number of targets (debugging)")
    parser.add_argument("--workers", type=int, default=6, help="Parallel downloads (default 6)")
    parser.add_argument("--selftest", action="store_true", help="Run offline selftest and exit")
    args = parser.parse_args()

    if args.dry_run:
        conn = common.connect()
        try:
            targets = fetch_targets(conn)
            per_term = Counter(t[2] for t in targets)
            print(f"{'term_id':>10} | sections")
            for term_id in sorted(per_term):
                print(f"{term_id:>10} | {per_term[term_id]}")
            print(f"{'total':>10} | {len(targets)}")
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM trace_courses WHERE term_id < 900 "
                            "AND (term_end_date IS NULL OR term_end_date = '')")
                excluded = cur.fetchone()[0]
            print(f"excluded (no term_end_date): {excluded}")
        finally:
            conn.close()
        return

    import requests

    session = requests.Session()
    session.headers.update(HEADERS)
    if args.cookie:
        session.headers["Cookie"] = args.cookie
    else:
        cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
        session.cookies.update(common.load_cookies(cookies_path))

    conn = common.connect()
    try:
        targets = fetch_targets(conn)
    finally:
        conn.close()

    if args.terms:
        wanted = {int(t) for t in args.terms.split(",")}
        targets = [t for t in targets if t[2] in wanted]
    if args.limit:
        targets = targets[:args.limit]

    halt = threading.Event()
    data_dir = common.DATA_DIR
    tally = Counter()
    failures = []

    try:
        from tqdm import tqdm
        progress = tqdm(total=len(targets))
    except ImportError:
        progress = None

    def process(triple):
        return triple, download_one(session, triple, data_dir, halt)

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for triple, status in pool.map(process, targets):
            tally[status] += 1
            if status in ("fail", "auth"):
                failures.append((*triple, status))
            done += 1
            if progress is not None:
                progress.update(1)
            elif done % 50 == 0 or done == len(targets):
                print(f"  {done}/{len(targets)}...")

    if progress is not None:
        progress.close()

    if failures:
        fail_path = os.path.join(data_dir, "xls", "failures.csv")
        os.makedirs(os.path.dirname(fail_path), exist_ok=True)
        with open(fail_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["course_id", "instructor_id", "term_id", "status"])
            w.writerows(failures)

    print(f"\n{dict(tally)}")
    if halt.is_set():
        print("Cookie expired — refresh cookies.txt and re-run (existing files are skipped automatically).")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()
