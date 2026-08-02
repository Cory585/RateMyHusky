"""Resumable Playwright downloader for ApplyWeb-era TRACE quantitative XLS reports.

ApplyWeb binds sessions to the browser fingerprint (copied cookies are rejected),
so downloads run as in-page fetch() inside a real headed Chrome with a persistent
profile under data/browser_profile/. Cache layout data/xls/{tid}/{cid}_{iid}_{tid}.xls
(existing valid files are skipped, so re-running resumes for free). On session
expiry the run pauses — re-login in the browser window and it resumes.

Usage:
  python scraper/applyweb_pipeline/scrape_xls.py --dry-run
  python scraper/applyweb_pipeline/scrape_xls.py [--terms 145,148] [--limit N]
  python scraper/applyweb_pipeline/scrape_xls.py --selftest
"""
import argparse
import base64
import csv
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

XLS_URL = ("https://www.applyweb.com/eval/EvalGatekeeper/EvalGatekeeper"
           "?service=QuantitativeXls&sp={cid}&sp={iid}&sp={tid}")
OLE_MAGIC = b"\xd0\xcf\x11\xe0"   # legacy .xls compound-file magic; login-page HTML fails this
TARGET_SQL = """SELECT DISTINCT course_id, instructor_id, term_id
                FROM trace_courses
                WHERE term_id < 900 AND term_end_date >= '2021-01-01'
                ORDER BY term_id, course_id, instructor_id"""
REPORTBROWSER_URL = "https://www.applyweb.com/eval/new/reportbrowser"

FETCH_BATCH_JS = """
async (urls) => {
  const one = async (url) => {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 60000);
    try {
      const r = await fetch(url, {credentials: "include", signal: ctl.signal});
      const bytes = new Uint8Array(await r.arrayBuffer());
      let bin = "";
      for (let i = 0; i < bytes.length; i += 0x8000)
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      return {status: r.status, b64: btoa(bin)};
    } catch (e) {
      return {status: 0, b64: ""};
    } finally { clearTimeout(timer); }
  };
  return Promise.all(urls.map(one));
}
"""


def build_url(cid, iid, tid):
    return XLS_URL.format(cid=cid, iid=iid, tid=tid)


def xls_path(data_dir, cid, iid, tid):
    return os.path.join(data_dir, "xls", str(tid), f"{cid}_{iid}_{tid}.xls")


def validate_xls_bytes(b):
    return b is not None and len(b) >= 512 and b[:4] == OLE_MAGIC


def write_xls(data_dir, triple, body):
    cid, iid, tid = triple
    path = xls_path(data_dir, cid, iid, tid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)


def split_pending(targets, data_dir):
    """Partition targets into (to-download, cached-count, first-cached-triple)."""
    pending, skips, first_cached = [], 0, None
    for t in targets:
        cid, iid, tid = t
        path = xls_path(data_dir, cid, iid, tid)
        ok = False
        if os.path.exists(path):
            with open(path, "rb") as f:
                ok = validate_xls_bytes(f.read())
        if ok:
            skips += 1
            if first_cached is None:
                first_cached = t
        else:
            pending.append(t)
    return pending, skips, first_cached


def normalize_results(raw):
    """Page-JS fetch results [{status, b64}] -> [(status, bytes)]; malformed -> (0, b'')."""
    out = []
    for r in raw:
        try:
            out.append((int(r.get("status", 0)), base64.b64decode(r.get("b64", "") or "")))
        except Exception:
            out.append((0, b""))
    return out


def run_downloads(pending, data_dir, fetch_batch, probe, wait_for_login,
                  batch_size=6, retries=2, on_ok=None, progress_cb=None):
    """Download pending triples in batches. Returns (tally, failures).

    A non-XLS response is *suspected* auth loss; probe() disambiguates:
    probe dead -> pause via wait_for_login() and retry the same files for free;
    probe alive -> the file itself is bad -> normal retry-then-fail path.
    """
    tally = Counter()
    failures = []
    for start in range(0, len(pending), batch_size):
        remaining = list(pending[start:start + batch_size])
        attempts = 0
        while remaining:
            results = list(fetch_batch([build_url(*t) for t in remaining]))[:len(remaining)]
            results += [(0, b"")] * (len(remaining) - len(results))
            bad = []
            for t, (status, body) in zip(remaining, results):
                if status == 200 and validate_xls_bytes(body):
                    write_xls(data_dir, t, body)
                    tally["ok"] += 1
                    if on_ok:
                        on_ok(t)
                    if progress_cb:
                        progress_cb()
                else:
                    bad.append(t)
            if not bad:
                break
            if not probe():              # session dead: pause; retry doesn't cost an attempt
                wait_for_login()
                remaining = bad
                continue
            attempts += 1
            if attempts > retries:       # session fine: content/transient failure
                for t in bad:
                    tally["fail"] += 1
                    failures.append((*t, "fail"))
                    if progress_cb:
                        progress_cb()
                break
            remaining = bad
    return tally, failures


class BrowserFetcher:
    """Real headed Chrome session; downloads run as in-page fetch() so they carry
    the browser's own TLS/client fingerprint (copied cookies get rejected)."""

    def __init__(self, profile_dir, sentinel_triples):
        self.profile_dir = profile_dir
        self.sentinel_urls = [build_url(*t) for t in sentinel_triples]
        self._good_seen = False
        self._pw = self._ctx = self.page = None

    def start(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                self.profile_dir, channel="chrome", headless=False)
        except Exception as e:
            self._pw.stop()
            sys.exit("Could not launch Chrome via Playwright (channel='chrome').\n"
                     "Google Chrome must be installed.\n" + str(e))
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.goto(REPORTBROWSER_URL, wait_until="domcontentloaded")

    def fetch_batch(self, urls):
        try:
            return normalize_results(self.page.evaluate(FETCH_BATCH_JS, urls))
        except Exception:
            return [(0, b"")] * len(urls)

    def probe(self):
        for i, url in enumerate(self.sentinel_urls):
            try:
                status, body = self.fetch_batch([url])[0]
            except Exception:
                continue
            if status == 200 and validate_xls_bytes(body):
                if i != 0:
                    self.sentinel_urls.insert(0, self.sentinel_urls.pop(i))
                return True
        return False

    def ensure_login(self):
        if not self.probe():
            self.wait_for_login()

    def wait_for_login(self):
        print("\nSession not active — log in to ApplyWeb in the browser window "
              "(NEU SSO + Duo). The run resumes automatically.")
        try:
            self.page.bring_to_front()
            self.page.goto(REPORTBROWSER_URL, wait_until="domcontentloaded")
        except Exception:
            pass
        waited = 0
        while True:
            try:
                alive = bool(self._ctx.pages)
            except Exception:
                alive = False
            if not alive:
                sys.exit("Browser window closed — re-run to resume (finished files are cached).")
            time.sleep(3)
            if self.probe():
                print("Session active — resuming.")
                return
            waited += 3
            if waited % 30 == 0:
                print(f"  still waiting for login... (probe: {self.sentinel_urls[0]})")

    def remember_good(self, triple):
        if not self._good_seen:
            self._good_seen = True
            self.sentinel_urls = [build_url(*triple)]

    def close(self):
        for closer in ((lambda: self._ctx.close()), (lambda: self._pw.stop())):
            try:
                closer()
            except Exception:
                pass


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

    bf = BrowserFetcher("unused_profile_dir", [(1, 2, 3), (4, 5, 6)])
    check("sentinel_urls from ctor triples",
          bf.sentinel_urls == [build_url(1, 2, 3), build_url(4, 5, 6)])
    bf.remember_good((7, 8, 9))
    bf.remember_good((10, 11, 12))
    check("remember_good replaces sentinel list once", bf.sentinel_urls == [build_url(7, 8, 9)])

    good_body = OLE_MAGIC + b"\x00" * 600
    bf = BrowserFetcher("unused_profile_dir", [(1, 2, 3), (4, 5, 6)])
    url_bad, url_good = build_url(1, 2, 3), build_url(4, 5, 6)
    bf.fetch_batch = lambda urls: [(200, b"<html>") if u == url_bad else (200, good_body) for u in urls]
    check("probe: first bad, second good -> True + promotes good to front",
          bf.probe() is True and bf.sentinel_urls[0] == url_good)

    bf = BrowserFetcher("unused_profile_dir", [(1, 2, 3), (4, 5, 6)])
    bf.fetch_batch = lambda urls: [(200, b"<html>") for _ in urls]
    check("probe: all candidates bad -> False", bf.probe() is False)

    bf = BrowserFetcher("unused_profile_dir", [(1, 2, 3), (4, 5, 6)])
    def _raise_first(urls):
        if urls[0] == url_bad:
            raise RuntimeError("boom")
        return [(200, good_body)]
    bf.fetch_batch = _raise_first
    check("probe: exception on one candidate doesn't skip the rest", bf.probe() is True)

    bf2 = BrowserFetcher("unused_profile_dir", [(1, 2, 3)])
    check("fetch_batch guards evaluate errors", bf2.fetch_batch(["u1", "u2"]) == [(0, b""), (0, b"")])

    bf3 = BrowserFetcher("unused_profile_dir", [(1, 2, 3)])
    try:
        bf3.wait_for_login()
        check("wait_for_login exits when browser closed", False)
    except SystemExit:
        check("wait_for_login exits when browser closed", True)

    import base64 as _b64
    check("normalize decodes",
          normalize_results([{"status": 200, "b64": _b64.b64encode(b"abc").decode()}]) == [(200, b"abc")])
    check("normalize malformed -> (0, b'')",
          normalize_results([{"status": "x", "b64": "!!!"}, {}]) == [(0, b""), (0, b"")])

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        good = OLE_MAGIC + b"\x00" * 600
        write_xls(td, (31, 32, 33), good)
        pending, skips, first = split_pending([(31, 32, 33), (34, 35, 36)], td)
        check("write_xls + split_pending skip cached",
              skips == 1 and first == (31, 32, 33) and pending == [(34, 35, 36)]
              and open(xls_path(td, 31, 32, 33), "rb").read() == good)
        pending2, skips2, first2 = split_pending([(34, 35, 36)], td)
        check("split_pending nothing cached", skips2 == 0 and first2 is None and pending2 == [(34, 35, 36)])

        class _Fake:
            """Scripted fetch rounds + probe answers; counts login pauses."""
            def __init__(self, rounds, probes):
                self.rounds, self.probes = list(rounds), list(probes)
                self.logins = 0
                self.batches = []
            def fetch_batch(self, urls):
                self.batches.append(list(urls))
                return self.rounds.pop(0)
            def probe(self):
                return self.probes.pop(0)
            def wait_for_login(self):
                self.logins += 1

        f = _Fake([[(200, good)]], [])
        tally, failures = run_downloads([(41, 42, 43)], td, f.fetch_batch, f.probe, f.wait_for_login)
        check("ok download writes file",
              tally["ok"] == 1 and not failures
              and open(xls_path(td, 41, 42, 43), "rb").read() == good)

        f = _Fake([[(200, b"<html>")]] * 3, [True, True, True])
        tally, failures = run_downloads([(44, 45, 46)], td, f.fetch_batch, f.probe, f.wait_for_login)
        check("bad content w/ live session: 3 rounds then fail",
              tally["fail"] == 1 and failures == [(44, 45, 46, "fail")]
              and len(f.batches) == 3 and f.logins == 0)

        f = _Fake([[(401, b"")], [(200, good)]], [False])
        tally, failures = run_downloads([(47, 48, 49)], td, f.fetch_batch, f.probe, f.wait_for_login)
        check("401 + dead probe: pause, re-login, resume, no fail",
              tally["ok"] == 1 and not failures and f.logins == 1)

        f = _Fake([[(200, b"<html>login page")], [(200, good)]], [False])
        tally, failures = run_downloads([(50, 51, 52)], td, f.fetch_batch, f.probe, f.wait_for_login)
        check("login-HTML-at-200 + dead probe pauses (not fail)",
              tally["ok"] == 1 and f.logins == 1 and not failures)

        f = _Fake([[(200, good), (200, b"x")], [(200, b"x")], [(200, b"x")]], [True, True, True])
        tally, failures = run_downloads([(53, 54, 55), (56, 57, 58)], td,
                                        f.fetch_batch, f.probe, f.wait_for_login, batch_size=2)
        check("mixed batch: ok written once, bad retried alone then fails",
              tally["ok"] == 1 and tally["fail"] == 1
              and failures == [(56, 57, 58, "fail")]
              and f.batches[1] == [build_url(56, 57, 58)])

        f = _Fake([[], [(200, good)]], [False])   # short result round = treated as bad
        tally, failures = run_downloads([(59, 60, 61)], td, f.fetch_batch, f.probe, f.wait_for_login)
        check("short result round padded and retried", tally["ok"] == 1 and f.logins == 1)

        seen, ticks = [], []
        f = _Fake([[(200, good), (200, b"x")], [(200, b"x")], [(200, b"x")]], [True, True, True])
        run_downloads([(62, 63, 64), (65, 66, 67)], td, f.fetch_batch, f.probe, f.wait_for_login,
                      batch_size=2, on_ok=seen.append, progress_cb=lambda: ticks.append(1))
        check("on_ok + progress_cb fire", seen == [(62, 63, 64)] and len(ticks) == 2)

        f = _Fake([[(200, good), (200, good)], [(200, good)]], [])
        tally, failures = run_downloads([(68, 69, 70), (71, 72, 73), (74, 75, 76)], td,
                                        f.fetch_batch, f.probe, f.wait_for_login, batch_size=2)
        check("multi-batch: 3 pending @ batch_size=2 -> two batches",
              tally["ok"] == 3 and not failures
              and [len(b) for b in f.batches] == [2, 1])

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


def main():
    parser = argparse.ArgumentParser(description="Download ApplyWeb-era TRACE quantitative XLS reports.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List per-term target counts and exit; downloads nothing")
    parser.add_argument("--terms", help="Comma-separated term_id filter, e.g. 145,148")
    parser.add_argument("--limit", type=int, help="Limit number of targets (debugging)")
    parser.add_argument("--workers", type=int, default=6, help="Parallel in-page fetches per batch (default 6)")
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

    data_dir = common.DATA_DIR
    pending, skips, first_cached = split_pending(targets, data_dir)
    tally = Counter()
    if skips:
        tally["skip"] = skips
    print(f"{len(targets)} targets: {skips} cached, {len(pending)} to download")
    if not pending:
        print(f"\n{dict(tally)}")
        return

    sentinels = ([first_cached] if first_cached else []) + pending[:3]
    fetcher = BrowserFetcher(os.path.join(data_dir, "browser_profile"), sentinels[:3])
    fetcher.start()
    fetcher.ensure_login()

    try:
        from tqdm import tqdm
        progress = tqdm(total=len(targets), initial=skips)
    except ImportError:
        progress = None
    done = [0]

    def tick():
        done[0] += 1
        if progress is not None:
            progress.update(1)
        elif done[0] % 50 == 0 or done[0] == len(pending):
            print(f"  {done[0]}/{len(pending)}...")

    try:
        dl_tally, failures = run_downloads(
            pending, data_dir, fetcher.fetch_batch, fetcher.probe, fetcher.wait_for_login,
            batch_size=args.workers, on_ok=fetcher.remember_good, progress_cb=tick)
    finally:
        fetcher.close()
        if progress is not None:
            progress.close()
    tally.update(dl_tally)

    if failures:
        fail_path = os.path.join(data_dir, "xls", "failures.csv")
        os.makedirs(os.path.dirname(fail_path), exist_ok=True)
        with open(fail_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["course_id", "instructor_id", "term_id", "status"])
            w.writerows(failures)

    print(f"\n{dict(tally)}")
    if failures:
        print("Some sections failed with a live session — review failures.csv; "
              "re-running retries them (cache skips the rest).")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()
