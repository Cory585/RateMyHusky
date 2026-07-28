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
        title_el = block.find("h4", class_="FrequencyQuestionTitle")
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
            r.raise_for_status()
            return r
        except Exception as e:
            w = 5 * (attempt + 1)
            print(f"    ⚠ {type(e).__name__}, retry in {w}s ({attempt+1}/3)")
            time.sleep(w)
    return None


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
<span id="ctl00_lblPublishDateInfo"><strong>4/28/2026</strong></span>
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
    check("audience/responses", r["audience"] == 30 and r["responses"] == 20)
    check("section table parsed", r["sections"][0]["section"] == "Course Related Questions"
          and r["sections"][0]["questions"][0]["Course Mean"] == 4.5)
    check("comment kept, [No Response] dropped",
          [c["comment"] for c in r["comments"]] == ["Great lectures; loved it"])
    check("comment prompt attached", r["comments"][0]["prompt"] == "What were the strengths of this course?")
    check("demographics distribution", r["demographics"][0]["distribution"] == {"3-4": 12, "5-7": 8})
    check("score distribution counted", r["score_distributions"]["Online course materials were organized"] == {5: 1})

    s = term_summary([r, dict(r, term="Sprong 2026"), {"error": "download failed", "report_name": "x"}])
    check("summary counts terms", s["term_counts"] == {"Spring 2026": 1, "Sprong 2026": 1})
    check("summary counts errors", s["errors"] == 1)

    u, j, c = state_paths("scraper/data/raw", "Spring 2026")
    check("state files are term-named",
          u.endswith("Spring 2026.urls.json") and j.endswith("Spring 2026.results.json")
          and c.endswith("Spring 2026.csv"))

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


def state_paths(out_dir, term):
    return (os.path.join(out_dir, f"{term}.urls.json"),
            os.path.join(out_dir, f"{term}.results.json"),
            os.path.join(out_dir, f"{term}.csv"))


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
    if not test or "Sign in" in test.text:
        print("ERROR: Cookies expired.")
        sys.exit(1)
    print("Session valid!\n")

    # Step 1: Collect URLs
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE) as f:
            all_reports = json.load(f)
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
                with open(URLS_FILE, "w") as f: json.dump(all_reports, f)
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
            time.sleep(0.5)
        with open(URLS_FILE, "w") as f: json.dump(all_reports, f)

    # Step 2: Download & parse
    done_names = set()
    results = []
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE) as f:
            results = json.load(f)
        done_names = {r.get("report_name", "") for r in results}
        remaining = len(all_reports) - len(done_names)
        if remaining > 0:
            print(f"Resuming: {len(done_names)} done, {remaining} remaining.\n")
        else:
            print(f"All {len(done_names)} reports downloaded.\n")

    total = len(all_reports)
    new_dl = 0
    for i, report in enumerate(all_reports):
        if report["name"] in done_names: continue
        r = fetch(s, "GET", report["url"])
        if r:
            try:
                parsed = parse_report_html(r.text)
                parsed["report_name"] = report["name"]
                nc = len(parsed.get("comments", []))
                nd = len(parsed.get("demographics", []))
                ns = len(parsed.get("score_distributions", {}))
                results.append(parsed)
                new_dl += 1
                print(f"  [{i+1}/{total}] ✓ {report['name']} ({nc} comments, {ns} scored, {nd} demo)")
            except Exception as e:
                results.append({"report_name": report["name"], "error": str(e)})
                print(f"  [{i+1}/{total}] ✗ {e}")
        else:
            results.append({"report_name": report["name"], "error": "download failed"})
            print(f"  [{i+1}/{total}] ✗ download failed")
        if new_dl % 50 == 0 and new_dl > 0:
            with open(RESULTS_FILE, "w") as f:
                json.dump(results, f, ensure_ascii=False)
            print(f"  ... saved ({len(results)} reports)")
        time.sleep(0.4)

    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

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
