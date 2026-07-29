"""
TRACE Bluera scraper (term-named state files).

Usage:
  python scraper/trace_pipeline/scrape.py --term "Spring 2026" --rid <guid>
"""

import requests
import json
import time
import re
import sys
import os
import csv
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from bs4 import BeautifulSoup
from html.parser import HTMLParser
import html as html_module

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from common import load_cookies

BASE_URL = "https://northeastern-bc.bluera.com"
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "raw")
DEFAULT_COOKIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
TIMEOUT = 60
BLOCK_STATUSES = (403, 429, 503)
CHECKPOINT_EVERY = 500  # results.json is ~100MB at term scale; saving it is not free

AGREE_MAP = {
    "Strongly Agree": 5, "Agree": 4, "Neutral": 3,
    "Disagree": 2, "Strongly Disagree": 1,
}
EFFECTIVENESS_MAP = {
    "Almost Always Effective": 5, "Usually Effective": 4,
    "Sometimes Effective": 3, "Rarely Effective": 2,
    "Almost Never Effective": 1,
}


class BlockTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_thead = self.in_tbody = self.in_cell = False
        self.headers, self.rows, self.cur_row, self.cell_text = [], [], [], ""
    def handle_starttag(self, tag, attrs):
        if tag == "thead": self.in_thead = True
        elif tag == "tbody": self.in_tbody = True
        elif tag == "tr": self.cur_row = []
        elif tag in ("th", "td"): self.in_cell = True; self.cell_text = ""
    def handle_endtag(self, tag):
        if tag == "thead": self.in_thead = False
        elif tag == "tbody": self.in_tbody = False
        elif tag == "tr":
            if self.in_thead and self.cur_row: self.headers = self.cur_row
            elif self.in_tbody and self.cur_row: self.rows.append(self.cur_row)
        elif tag in ("th", "td") and self.in_cell:
            self.cur_row.append(self.cell_text.strip()); self.in_cell = False
    def handle_data(self, data):
        if self.in_cell: self.cell_text += data


def text_to_score(text):
    text = text.strip()
    if text in AGREE_MAP: return AGREE_MAP[text]
    if text in EFFECTIVENESS_MAP: return EFFECTIVENESS_MAP[text]
    return None


def parse_demographics(soup):
    """Parse frequency/demographics blocks (attendance, hours per week)."""
    demographics = []
    for block in soup.find_all("div", class_="FrequencyBlock_FullMain"):
        # Bluera renders the title as h4 in pre-July-2026 reports, h5 since — accept both.
        title_el = block.find(["h4", "h5"], class_="FrequencyQuestionTitle")
        if not title_el:
            continue
        question = title_el.get_text(strip=True)

        distribution = {}
        for li in block.find_all("li"):
            label_div = li.find("div", class_="frequency-data-item-choice-text")
            count_div = li.find("div", class_="frequency-data-item-choice-nb")
            if label_div and count_div:
                label = label_div.get_text(strip=True)
                try:
                    count = int(count_div.get_text(strip=True))
                except ValueError:
                    count = 0
                distribution[label] = count

        if distribution:
            demographics.append({
                "question": question,
                "distribution": distribution,
            })
    return demographics


def parse_report_html(html_text):
    soup = BeautifulSoup(html_text, "html.parser")

    # Metadata
    title_tag = soup.find("title")
    title_text = title_tag.text.strip() if title_tag else ""
    m = re.search(r"Student TRACE report for (.+)", title_text)
    course_info = m.group(1).strip() if m else title_text

    # Term
    term = ""
    for sp in soup.find_all("span", id=re.compile(r"ProjectTitle")):
        t = sp.get_text(strip=True)
        if t and t != "Project Title":
            term = t; break

    # Created date
    created_date = ""
    created_span = soup.find("span", id=re.compile(r"lbPublishDateInfo"))
    if created_span:
        strong = created_span.find("strong")
        if strong: created_date = strong.get_text(strip=True)

    # Audience & responses
    aud_el = soup.find("span", id=re.compile(r"lblInvited"))
    resp_el = soup.find("span", id=re.compile(r"lblResponded"))
    audience = int(aud_el.text.strip()) if aud_el else None
    responses = int(resp_el.text.strip()) if resp_el else None

    # Section headings
    headings = [h3.find("strong").get_text(strip=True)
                for h3 in soup.find_all("h3") if h3.find("strong")]

    # Summary tables
    tables = re.findall(r"<table class='block-table[^']*'>.*?</table>", html_text, re.DOTALL)
    sections = []
    for i, block in enumerate(tables):
        p = BlockTableParser(); p.feed(block)
        name = headings[i] if i < len(headings) else f"Section {i+1}"
        questions = []
        for row in p.rows:
            if len(row) < 2: continue
            q = {"question": row[0]}
            for j, h in enumerate(p.headers[1:], 1):
                if j < len(row):
                    v = row[j].strip()
                    try: v = float(v) if "." in v and "%" not in v else v
                    except: pass
                    try: v = int(v) if isinstance(v, str) and v.isdigit() else v
                    except: pass
                    q[h.strip()] = v
            questions.append(q)
        sections.append({"section": name, "questions": questions})

    # Comments
    comments = []
    for block in soup.find_all("div", class_="CommentBlockRow"):
        prev = block.find_previous("h4", class_="ReportBlockTitle")
        prompt = ""
        if prev:
            span = prev.find("span", id=re.compile(r"lblBlockTitle"))
            if span:
                prompt = span.get_text(strip=True)
                if prompt == "-": prompt = ""
        for td in block.find_all("td"):
            div = td.find("div")
            if div:
                text = div.get_text(strip=True)
                if text and text != "[No Response]":
                    comments.append({"prompt": prompt, "comment": html_module.unescape(text)})

    # Score distributions from individual responses
    score_dist = {}
    for sheet in soup.find_all("div", class_="RespS_Sheet"):
        for li in sheet.find_all("li", class_="RespS_QuestionTitle_ListItem"):
            q_rows = li.find_all("span", class_="RespS_QuestionRow_font")
            if q_rows:
                resp_spans = li.find_all("span", class_="RespS_Resp_font")
                for idx, qrow in enumerate(q_rows):
                    question = qrow.get_text(strip=True)
                    resp_text = resp_spans[idx].get_text(strip=True) if idx < len(resp_spans) else ""
                    score = text_to_score(resp_text)
                    if score is not None:
                        score_dist.setdefault(question, []).append(score)
            else:
                title_div = li.find("div", class_="RespS_QuestionTitle_font")
                question = ""
                if title_div:
                    for sp in title_div.find_all("span", recursive=False):
                        if "RespS_QuestionTitle_index" not in (sp.get("class") or []):
                            t = sp.get_text(strip=True)
                            if t and t != "-": question = t
                resp_span = li.find("span", class_="RespS_Resp_font")
                if resp_span:
                    resp_text = resp_span.get_text(strip=True)
                    score = text_to_score(resp_text)
                    if score is not None and question:
                        score_dist.setdefault(question, []).append(score)

    # Demographics
    demographics = parse_demographics(soup)

    return {
        "course_info": course_info,
        "term": term,
        "created_date": created_date,
        "audience": audience,
        "responses": responses,
        "sections": sections,
        "comments": comments,
        "score_distributions": {q: dict(Counter(scores)) for q, scores in score_dist.items()},
        "demographics": demographics,
    }


def fetch(session, method, url, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)
    for attempt in range(3):
        try:
            r = session.get(url, **kwargs) if method == "GET" else session.post(url, **kwargs)
            if r.status_code in BLOCK_STATUSES:
                return r  # rate-limit/WAF block: retrying only deepens it
            r.raise_for_status()
            return r
        except Exception as e:
            w = 5 * (attempt + 1)
            print(f"    ⚠ {type(e).__name__}, retry in {w}s ({attempt+1}/3)")
            time.sleep(w)
    return None


def make_worker_session(cookies):
    """One session per download thread. Bluera's ASP.NET session lock serializes
    every request sharing an ASP.NET_SessionId, so workers must not share one —
    omit it and the server hands each worker its own session."""
    s = requests.Session()
    s.cookies.update({k: v for k, v in cookies.items() if k != "ASP.NET_SessionId"})
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    return s


def scrape_one(session, report, fetch_fn=fetch):
    """Download+parse one report. Returns (result, abort): abort is None, "expired"
    or "blocked:<code>". Per-report failures become error rows; expiry and blocks
    must stop the whole run instead of filling results with thousands of them."""
    r = fetch_fn(session, "GET", report["url"])
    if r is None:
        return {"report_name": report["name"], "error": "download failed"}, None
    if r.status_code in BLOCK_STATUSES:
        return None, f"blocked:{r.status_code}"
    if is_session_expired(r.text):
        return None, "expired"
    try:
        parsed = parse_report_html(r.text)
        parsed["report_name"] = report["name"]
        return parsed, None
    except Exception as e:
        return {"report_name": report["name"], "error": str(e)}, None


def results_to_csv(results, output_file):
    fieldnames = [
        "term", "created_date", "course_info", "audience",
        "section", "question",
        "Number of Responses",
        "Course Mean", "Dept. Mean", "Univ. Mean",
        "Course Median", "Dept. Median", "Univ. Median",
        "count_5", "count_4", "count_3", "count_2", "count_1",
        "comment_prompt", "comments_json",
        "demographics_json",
    ]
    rows = []
    for r in results:
        if "error" in r: continue
        base = {
            "term": r.get("term", ""),
            "created_date": r.get("created_date", ""),
            "course_info": r.get("course_info", ""),
            "audience": r.get("audience", ""),
        }
        score_dists = r.get("score_distributions", {})
        comments_by_prompt = {}
        for c in r.get("comments", []):
            comments_by_prompt.setdefault(c["prompt"], []).append(c["comment"])

        # Summary rows
        for s in r.get("sections", []):
            for q in s.get("questions", []):
                row = {**base, "section": s["section"]}
                qtext = q.get("question", "")
                if qtext.isdigit() and "Effectiveness" in s["section"]:
                    row["question"] = "What is your overall rating of this instructor's teaching effectiveness?"
                    row["Number of Responses"] = int(qtext)
                    keys = [k for k in q if k != "question"]
                    vals = [q[k] for k in keys]
                    correct = ["_skip", "Course Mean", "Dept. Mean", "Univ. Mean",
                               "Course Median", "Dept. Median", "Univ. Median"]
                    for i, k in enumerate(correct):
                        if k != "_skip":
                            row[k] = vals[i] if i < len(vals) else ""
                    qtext = row["question"]
                else:
                    row["question"] = qtext
                    row["Number of Responses"] = q.get("Number of Responses", "")
                    for k in ["Course Mean", "Dept. Mean", "Univ. Mean",
                              "Course Median", "Dept. Median", "Univ. Median"]:
                        row[k] = q.get(k, "")
                dist = score_dists.get(qtext, {})
                row["count_5"] = dist.get(5, dist.get("5", 0))
                row["count_4"] = dist.get(4, dist.get("4", 0))
                row["count_3"] = dist.get(3, dist.get("3", 0))
                row["count_2"] = dist.get(2, dist.get("2", 0))
                row["count_1"] = dist.get(1, dist.get("1", 0))
                rows.append(row)

        # Comment rows
        for prompt, comment_list in comments_by_prompt.items():
            row = {**base, "section": "Comments",
                   "comment_prompt": prompt,
                   "comments_json": json.dumps(comment_list, ensure_ascii=False)}
            rows.append(row)

        # Demographics rows
        for demo in r.get("demographics", []):
            row = {**base, "section": "Demographics",
                   "question": demo["question"],
                   "demographics_json": json.dumps(demo["distribution"], ensure_ascii=False)}
            rows.append(row)

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} rows to {output_file}")


_FIXTURE_HTML = """
<html><head><title>Student TRACE report for CS3500-01 Object-Oriented Design (Jane Doe)</title></head>
<body>
<span id="ctl00_ProjectTitle">Spring 2026</span>
<span id="ctl00_lbPublishDateInfo"><strong>4/28/2026</strong></span>
<span id="ctl00_lblInvited">30</span><span id="ctl00_lblResponded">20</span>
<h3><strong>Course Related Questions</strong></h3>
<table class='block-table'>
<thead><tr><th>Question</th><th>Number of Responses</th><th>Course Mean</th><th>Dept. Mean</th><th>Univ. Mean</th><th>Course Median</th><th>Dept. Median</th><th>Univ. Median</th></tr></thead>
<tbody><tr><td>Online course materials were organized</td><td>20</td><td>4.5</td><td>4.1</td><td>4.2</td><td>5.0</td><td>4.0</td><td>4.0</td></tr></tbody>
</table>
<h4 class="ReportBlockTitle"><span id="ctl00_lblBlockTitle">What were the strengths of this course?</span></h4>
<div class="CommentBlockRow"><table><tr><td><div>Great lectures; loved it</div></td><td><div>[No Response]</div></td></tr></table></div>
<div class="FrequencyBlock_FullMain"><h4 class="FrequencyQuestionTitle">How many hours per week did you spend on this course?</h4>
<ul><li><div class="frequency-data-item-choice-text">3-4</div><div class="frequency-data-item-choice-nb">12</div></li>
<li><div class="frequency-data-item-choice-text">5-7</div><div class="frequency-data-item-choice-nb">8</div></li></ul></div>
<div class="FrequencyBlock_FullMain"><h5 class="FrequencyQuestionTitle">How often did you attend this class both in-person and remotely?</h5>
<ul><li><div class="frequency-data-item-choice-text text-ellipsis">80-100%</div><div class="frequency-data-item-choice-nb">10</div></li></ul></div>
<div class="RespS_Sheet"><ul><li class="RespS_QuestionTitle_ListItem">
<div class="RespS_QuestionTitle_font"><span class="RespS_QuestionTitle_index">1</span><span>Online course materials were organized</span></div>
<span class="RespS_Resp_font">Strongly Agree</span></li></ul></div>
</body></html>
"""

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    r = parse_report_html(_FIXTURE_HTML)
    check("course_info parsed", r["course_info"] == "CS3500-01 Object-Oriented Design (Jane Doe)")
    check("term parsed from ProjectTitle", r["term"] == "Spring 2026")
    check("created_date parsed", r["created_date"] == "4/28/2026")
    check("audience/responses", r["audience"] == 30 and r["responses"] == 20)
    check("section table parsed", r["sections"][0]["section"] == "Course Related Questions"
          and r["sections"][0]["questions"][0]["Course Mean"] == 4.5)
    check("comment kept, [No Response] dropped",
          [c["comment"] for c in r["comments"]] == ["Great lectures; loved it"])
    check("comment prompt attached", r["comments"][0]["prompt"] == "What were the strengths of this course?")
    check("demographics distribution", r["demographics"][0]["distribution"] == {"3-4": 12, "5-7": 8})
    check("demographics h5 title parsed (Bluera post-July-2026 markup)",
          r["demographics"][1]["distribution"] == {"80-100%": 10}
          and r["demographics"][1]["question"].startswith("How often did you attend"))
    check("score distribution counted", r["score_distributions"]["Online course materials were organized"] == {5: 1})

    s = term_summary([r, dict(r, term="Sprong 2026"), {"error": "download failed", "report_name": "x"}])
    check("summary counts terms", s["term_counts"] == {"Spring 2026": 1, "Sprong 2026": 1})
    check("summary counts errors", s["errors"] == 1)

    u, j, c = state_paths("scraper/data/raw", "Spring 2026")
    check("state files are term-named",
          u.endswith("Spring 2026.urls.json") and j.endswith("Spring 2026.results.json")
          and c.endswith("Spring 2026.csv"))

    check("session-expiry detector flags a login page",
          is_session_expired("<html><body><h1>Sign in</h1></body></html>") is True)
    check("session-expiry detector passes a real report page",
          is_session_expired(_FIXTURE_HTML) is False)

    # ── parallel download helpers ──
    ws = make_worker_session({"ASP.NET_SessionId": "shared", "session_token": "keep"})
    check("worker session omits the shared ASP.NET_SessionId (server serializes on it)",
          ws.cookies.get("ASP.NET_SessionId") is None and ws.cookies.get("session_token") == "keep")

    class _Resp:
        def __init__(self, status=200, text=""):
            self.status_code, self.text = status, text
        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    def fake_fetch(status=200, text=_FIXTURE_HTML):
        def f(session, method, url, **kw):
            return None if status is None else _Resp(status, text)
        return f

    rep = {"name": "CS3500-01", "url": "http://x/report"}

    res, why = scrape_one(None, rep, fetch_fn=fake_fetch())
    check("scrape_one parses a report and tags it with report_name",
          why is None and res["report_name"] == "CS3500-01" and res["term"] == "Spring 2026")

    res, why = scrape_one(None, rep, fetch_fn=fake_fetch(status=None))
    check("scrape_one logs a download failure without aborting the run",
          why is None and res["error"] == "download failed")

    res, why = scrape_one(None, rep, fetch_fn=fake_fetch(text="<html><body>Sign in</body></html>"))
    check("scrape_one aborts the run on mid-run session expiry",
          res is None and why == "expired")

    blocked = [scrape_one(None, rep, fetch_fn=fake_fetch(status=c)) for c in (403, 429, 503)]
    check("scrape_one aborts on WAF block statuses instead of logging error rows",
          all(r is None and w == f"blocked:{c}"
              for (r, w), c in zip(blocked, (403, 429, 503))))

    tries = {"n": 0}
    class _BlockSession:
        def get(self, url, **kw):
            tries["n"] += 1
            return _Resp(429, "blocked")
    _saved_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        br = fetch(_BlockSession(), "GET", "http://x")
    finally:
        time.sleep = _saved_sleep
    check("fetch returns a blocked response at once (retrying deepens the block)",
          br.status_code == 429 and tries["n"] == 1)

    _saved_parse = globals()["parse_report_html"]
    globals()["parse_report_html"] = lambda t: (_ for _ in ()).throw(ValueError("bad table"))
    try:
        res, why = scrape_one(None, rep, fetch_fn=fake_fetch())
    finally:
        globals()["parse_report_html"] = _saved_parse
    check("scrape_one logs a parse failure as an error row",
          why is None and res["error"] == "bad table")

    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "roundtrip.json")
        save_json(p, [{"comment": "non‑breaking hyphen"}], indent=2)
        check("json state files round-trip non-cp1252 chars (Windows crash regression)",
              load_json(p)[0]["comment"] == "non‑breaking hyphen")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


def state_paths(out_dir, term):
    return (os.path.join(out_dir, f"{term}.urls.json"),
            os.path.join(out_dir, f"{term}.results.json"),
            os.path.join(out_dir, f"{term}.csv"))


def save_json(path, data, indent=None):
    """Windows defaults text files to cp1252, which can't encode chars TRACE comments
    contain (e.g. U+2011 non-breaking hyphen) — state files must be explicit UTF-8."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_session_expired(html_text):
    """A mid-run cookie expiry returns the Bluera sign-in page instead of a report.
    Real report pages always carry a ProjectTitle span; login pages say 'Sign in'."""
    return "Sign in" in html_text or "ProjectTitle" not in html_text


def term_summary(results):
    term_counts = Counter(r.get("term", "") for r in results if "error" not in r)
    return {"reports": sum(1 for r in results if "error" not in r),
            "errors": sum(1 for r in results if "error" in r),
            "error_names": [r.get("report_name", "?") for r in results if "error" in r][:10],
            "term_counts": dict(term_counts),
            "comments": sum(len(r.get("comments", [])) for r in results if "error" not in r),
            "scored": sum(len(r.get("score_distributions", {})) for r in results if "error" not in r)}


def main():
    parser = argparse.ArgumentParser(description="Scrape TRACE reports from Bluera for a given term.")
    parser.add_argument("--term", required=True, help='Term name, e.g. "Spring 2026"')
    parser.add_argument("--rid", required=True, help="Bluera report-list rid GUID")
    parser.add_argument("--cookies", default=DEFAULT_COOKIES, help="Path to cookie header file")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Output directory for state/CSV")
    parser.add_argument("--workers", type=int, default=6,
                        help="Parallel report downloads (default 6, same as a browser)")
    parser.add_argument("--selftest", action="store_true", help="Run offline selftest and exit")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    cookies = load_cookies(args.cookies)
    URLS_FILE, RESULTS_FILE, OUTPUT_CSV = state_paths(args.out_dir, args.term)
    LIST_URL = f"{BASE_URL}/rpvlf.aspx?rid={args.rid}&regl=en-US&haslang=true"

    s = requests.Session()
    s.cookies.update(cookies)
    s.headers.update({"User-Agent": "Mozilla/5.0"})

    print("Testing session...")
    test = fetch(s, "GET", LIST_URL)
    if test is not None and test.status_code in BLOCK_STATUSES:
        print(f"ERROR: server returned HTTP {test.status_code} (rate-limit/WAF block). "
              f"Wait a few minutes and re-run.")
        sys.exit(1)
    if not test or "Sign in" in test.text:
        print("ERROR: Cookies expired.")
        sys.exit(1)
    print("Session valid!\n")

    # Step 1: Collect URLs
    if os.path.exists(URLS_FILE):
        all_reports = load_json(URLS_FILE)
        print(f"Loaded {len(all_reports)} URLs from {URLS_FILE}\n")
    else:
        all_reports = []
        resp = test
        page = 1
        while True:
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.find_all("a", href=re.compile(r"rpvf-eng\.aspx")):
                href = link["href"]
                if not href.startswith("http"): href = BASE_URL + "/" + href
                all_reports.append({"name": link.get_text(strip=True), "url": href.replace("&amp;", "&")})
            print(f"  Page {page}: total {len(all_reports)} URLs")
            if page % 10 == 0:
                save_json(URLS_FILE, all_reports)
            next_btn = None
            for inp in soup.find_all("input", id=re.compile(r"btnNext")):
                if not inp.has_attr("disabled") and "Disabled" not in str(inp.get("class", "")):
                    next_btn = inp; break
            if not next_btn:
                print(f"\nAll pages collected! {len(all_reports)} URLs total.\n")
                break
            vs = soup.find("input", {"id": "__VIEWSTATE"})["value"]
            ev = soup.find("input", {"id": "__EVENTVALIDATION"})["value"]
            vg = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"]
            resp = fetch(s, "POST", LIST_URL, data={
                "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__VIEWSTATE": vs,
                "__VIEWSTATEGENERATOR": vg, "__VIEWSTATEENCRYPTED": "",
                "__EVENTVALIDATION": ev, next_btn["name"]: "",
            })
            if not resp:
                print(f"\nConnection lost at page {page}. Re-run to resume.")
                break
            page += 1
        save_json(URLS_FILE, all_reports)

    # Step 2: Download & parse
    done_names = set()
    results = []
    if os.path.exists(RESULTS_FILE):
        results = load_json(RESULTS_FILE)
        done_names = {r.get("report_name", "") for r in results}
        remaining = len(all_reports) - len(done_names)
        if remaining > 0:
            print(f"Resuming: {len(done_names)} done, {remaining} remaining.\n")
        else:
            print(f"All {len(done_names)} reports downloaded.\n")

    total = len(all_reports)
    pending = [r for r in all_reports if r["name"] not in done_names]
    abort = None

    if pending:
        print(f"Downloading {len(pending)} reports with {args.workers} workers...")
        local = threading.local()
        stop = threading.Event()

        def run_one(report):
            if stop.is_set():
                return None, "stopped"
            if not hasattr(local, "session"):
                local.session = make_worker_session(cookies)
            result, why = scrape_one(local.session, report)
            if why:
                stop.set()
            return result, why

        done = len(done_names)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for report, (result, why) in zip(pending, pool.map(run_one, pending)):
                if why:
                    abort = abort or why
                    continue
                results.append(result)
                done += 1
                if "error" in result:
                    print(f"  [{done}/{total}] ✗ {report['name']}: {result['error']}")
                else:
                    nc = len(result.get("comments", []))
                    nd = len(result.get("demographics", []))
                    ns = len(result.get("score_distributions", {}))
                    print(f"  [{done}/{total}] ✓ {report['name']} ({nc} comments, {ns} scored, {nd} demo)")
                if done % CHECKPOINT_EVERY == 0:
                    save_json(RESULTS_FILE, results)
                    print(f"  ... saved ({len(results)} reports)")

    save_json(RESULTS_FILE, results, indent=2)

    if abort == "expired":
        print(f"\nSession expired mid-run ({len(results)}/{total} saved). "
              f"Refresh cookies.txt and re-run the same command to resume.")
        sys.exit(1)
    if abort:
        print(f"\nServer returned HTTP {abort.split(':')[1]} (rate-limit/WAF block) mid-run "
              f"({len(results)}/{total} saved). Wait a few minutes, then re-run the same command "
              f"to resume, with --workers {max(1, args.workers // 2)} if it recurs.")
        sys.exit(1)

    # Step 3: CSV
    print("\nConverting to CSV...")
    results_to_csv(results, OUTPUT_CSV)

    summary = term_summary(results)
    print(f"\n{'='*60}")
    print(f"DONE! {summary['reports']}/{total} reports")
    print(f"JSON: {RESULTS_FILE}")
    print(f"CSV:  {OUTPUT_CSV}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"{'='*60}")

    off_term = [t for t in summary["term_counts"] if t != args.term]
    if off_term:
        print(f"WARNING: reports found with term(s) != '{args.term}': {off_term}")

    if summary["errors"] > 0:
        print(f"\n{summary['errors']} report(s) failed to download. "
              f"Re-run the same command to resume from {RESULTS_FILE}.")
        sys.exit(1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()
