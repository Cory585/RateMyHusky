"""
TRACE Report Scraper (Explorance Blue)
Uses Playwright to automate browser after manual login.

Install:
  pip install playwright pandas
  python -m playwright install chromium

Usage:
  python updated_trace_scraper.py
"""

import csv, time, re
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path("output_data")
OUTPUT_DIR.mkdir(exist_ok=True)

SCORES_FILE = OUTPUT_DIR / "trace_scores_new.csv"
COMMENTS_FILE = OUTPUT_DIR / "trace_comments_new.csv"
COURSES_FILE = OUTPUT_DIR / "trace_courses_new.csv"
PROGRESS_FILE = OUTPUT_DIR / "scrape_progress.txt"

DELAY = 2


def wait(seconds=DELAY):
    time.sleep(seconds)


def parse_report_title(title):
    """Parse 'TRACE report for CS5008-01 Data Str, Algo & App in CmpSys (Albert Lionelle)'"""
    m = re.match(
        r"TRACE report for\s+(\w+)[-:](\d+)\s+(.+?)\s*\((.+?)\)\s*$",
        title,
    )
    if m:
        return {
            "course_code": m.group(1),
            "section": m.group(2),
            "course_name": m.group(3).strip().rstrip(","),
            "instructor": m.group(4).strip(),
        }
    m2 = re.match(r"TRACE report for\s+(\S+)\s+(.+?)\s*\((.+?)\)\s*$", title)
    if m2:
        return {
            "course_code": m2.group(1),
            "section": "",
            "course_name": m2.group(2).strip(),
            "instructor": m2.group(3).strip(),
        }
    return {
        "course_code": "",
        "section": "",
        "course_name": title,
        "instructor": "",
    }


def get_report_frame(page):
    """Find the frame containing the report list — could be main page or an iframe."""
    # First try the main page
    try:
        anchors = page.query_selector_all('a[href*="rpvf-eng.aspx"]')
        if anchors:
            return page
    except:
        pass

    # Check all iframes
    for frame in page.frames:
        try:
            anchors = frame.query_selector_all('a[href*="rpvf-eng.aspx"]')
            if anchors:
                print(f"  Found reports in iframe: {frame.url[:80]}")
                return frame
        except:
            continue

    # Last resort: try looking for any text containing "TRACE report"
    for frame in page.frames:
        try:
            text = frame.inner_text("body")
            if "TRACE report for" in text:
                print(f"  Found TRACE text in iframe: {frame.url[:80]}")
                return frame
        except:
            continue

    return page


def collect_all_report_links(frame, page):
    """Paginate through the report list and collect all <a> links."""
    all_links = []
    page_num = 1

    while True:
        wait(2)

        # Find all report links
        anchors = frame.query_selector_all('a[href*="rpvf-eng.aspx"]')
        # Also try without the specific href in case format differs
        if not anchors:
            anchors = frame.query_selector_all('a')

        found = 0
        for a in anchors:
            try:
                text = a.inner_text().strip()
                href = a.get_attribute("href") or ""
            except:
                continue
            if "TRACE report for" in text and href:
                if not href.startswith("http"):
                    # Build full URL from the frame's base
                    frame_url = frame.url if hasattr(frame, 'url') else page.url
                    base = frame_url.rsplit("/", 1)[0]
                    href = base + "/" + href
                all_links.append({"title": text, "url": href})
                found += 1

        print(f"  Page {page_num}: {found} reports (total: {len(all_links)})")

        if found == 0:
            print("  No reports found on this page — stopping.")
            break

        # Find next page button
        next_btn = None
        try:
            pager_links = frame.query_selector_all("a")
            for link in pager_links:
                try:
                    text = link.inner_text().strip()
                    aria = link.get_attribute("aria-label") or ""
                    if text == "›" or text == ">" or "Next" in aria or "next" in aria:
                        next_btn = link
                        break
                except:
                    continue
        except:
            pass

        if next_btn:
            try:
                next_btn.click()
                wait(2)
                page_num += 1
            except:
                break
        else:
            break

    print(f"\nTotal reports collected: {len(all_links)}")
    return all_links


def scrape_single_report(page):
    """Scrape ratings and comments from a single report page."""
    scores = []
    comments = []
    enrollment = 0
    completed = 0

    page.wait_for_load_state("networkidle")
    wait(2)

    # Expand all sections if there's a collapse/expand button
    try:
        expand_btns = page.query_selector_all("a, button, span")
        for btn in expand_btns:
            text = btn.inner_text().strip().upper()
            if "COLLAPSE/EXPAND ALL" in text or "EXPAND ALL" in text:
                btn.click()
                wait(1)
                break
    except:
        pass

    try:
        rating_btns = page.query_selector_all("a, button, span")
        for btn in rating_btns:
            text = btn.inner_text().strip().upper()
            if "COLLAPSE/EXPAND RATINGS" in text or "EXPAND RATINGS" in text:
                btn.click()
                wait(0.5)
    except:
        pass

    html = page.content()
    all_text = page.inner_text("body")

    # Extract enrollment and responses
    audience_match = re.search(r"Courses?\s*Audience:?\s*(\d+)", html)
    response_match = re.search(r"Responses?\s*Received:?\s*(\d+)", html)
    if audience_match:
        enrollment = int(audience_match.group(1))
    if response_match:
        completed = int(response_match.group(1))

    # Parse ratings from page text
    lines = all_text.split("\n")
    current_question = None
    current_section = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect section headers
        if any(kw in line.lower() for kw in [
            "questions to assess", "overall", "assessment of",
            "course assessment", "instructor assessment",
        ]):
            if not line[0].isdigit():
                current_section = line

        # Detect numbered questions
        q_match = re.match(r"^(\d+)\.\s+(.+)", line)
        if q_match:
            current_question = q_match.group(2).strip()
            continue

        # Detect student rating
        if current_question:
            rating_match = re.match(r"^Students?\s+([\d.]+)", line)
            if rating_match:
                mean = float(rating_match.group(1))
                scores.append({
                    "question": current_question,
                    "section_name": current_section,
                    "mean": mean,
                    "enrollment": enrollment,
                    "completed": completed,
                })
                current_question = None

    # Parse comments
    in_comments = False
    current_comment_question = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if re.match(r"^(What|Please|How|Describe|Any|Is there|Do you)", line) and len(line) > 20:
            in_comments = True
            current_comment_question = line
            continue

        if in_comments:
            if re.match(r"^(\d+\.\s|Questions? to|COLLAPSE|EXPAND|Overall|Rating Scale)", line):
                in_comments = False
                continue
            if len(line) > 5 and not re.match(r"^(Students?|Department|University|N/A)\s", line):
                comments.append({
                    "question": current_comment_question,
                    "comment": line,
                })

    return scores, comments, enrollment, completed


def load_progress():
    if PROGRESS_FILE.exists():
        return int(PROGRESS_FILE.read_text().strip())
    return 0


def save_progress(idx):
    PROGRESS_FILE.write_text(str(idx))


def save_data(scores, comments, courses):
    if scores:
        keys = ["course_code", "section", "course_name", "instructor",
                "question", "section_name", "mean", "enrollment", "completed"]
        with open(SCORES_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(scores)

    if comments:
        keys = ["course_code", "section", "instructor", "question", "comment"]
        with open(COMMENTS_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(comments)

    if courses:
        keys = ["course_code", "section", "course_name", "instructor",
                "enrollment", "completed"]
        with open(COURSES_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(courses)


def main():
    print("=" * 60)
    print("  TRACE Report Scraper (Explorance Blue)")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        page.goto("https://northeastern.bluera.com/northeastern/")
        print("\n1. Login with your NEU credentials.")
        print("2. Go to: Reports → All reports for students → View report")
        print("3. You should see the paginated list of TRACE reports.")
        input("\nPress Enter when you're on the report list page...")

        # Wait for everything to settle
        wait(3)
        page.wait_for_load_state("networkidle")
        wait(2)

        # Find the correct frame (might be inside an iframe)
        print("\nLooking for report list...")
        frame = get_report_frame(page)
        if frame == page:
            print("  Reports found on main page")
        
        # Collect all report links
        print("\nCollecting report links from all pages...")
        all_links = collect_all_report_links(frame, page)

        if not all_links:
            print("No reports found! Make sure you're on the correct page.")
            browser.close()
            return

        # Check for resume
        start_idx = load_progress()
        if start_idx > 0:
            print(f"\nResuming from report #{start_idx + 1} (previous progress found)")
            resp = input(f"Continue from #{start_idx + 1}? (y/n): ").strip().lower()
            if resp != "y":
                start_idx = 0

        all_scores = []
        all_comments = []
        all_courses = []
        errors = []

        for idx in range(start_idx, len(all_links)):
            report = all_links[idx]
            parsed = parse_report_title(report["title"])
            print(f"\n[{idx + 1}/{len(all_links)}] {report['title'][:80]}...")

            try:
                # Navigate directly to the report URL
                page.goto(report["url"])
                wait(3)

                scores, report_comments, enrollment, completed = scrape_single_report(page)

                for s in scores:
                    s.update({
                        "course_code": parsed["course_code"],
                        "section": parsed["section"],
                        "course_name": parsed["course_name"],
                        "instructor": parsed["instructor"],
                    })
                    all_scores.append(s)

                for c in report_comments:
                    c.update({
                        "course_code": parsed["course_code"],
                        "section": parsed["section"],
                        "instructor": parsed["instructor"],
                    })
                    all_comments.append(c)

                all_courses.append({
                    "course_code": parsed["course_code"],
                    "section": parsed["section"],
                    "course_name": parsed["course_name"],
                    "instructor": parsed["instructor"],
                    "enrollment": enrollment,
                    "completed": completed,
                })

                print(f"  ✓ {len(scores)} scores, {len(report_comments)} comments, "
                      f"enrollment={enrollment}, responses={completed}")

                # Save progress every 10 reports
                if (idx + 1) % 10 == 0:
                    save_data(all_scores, all_comments, all_courses)
                    save_progress(idx + 1)
                    print(f"  💾 Progress saved ({idx + 1}/{len(all_links)})")

            except Exception as e:
                print(f"  ✗ Error: {e}")
                errors.append(report["title"])

        # Final save
        save_data(all_scores, all_comments, all_courses)
        save_progress(len(all_links))

        print(f"\n{'=' * 60}")
        print("DONE!")
        print(f"  Scores:   {len(all_scores)} rows → {SCORES_FILE}")
        print(f"  Comments: {len(all_comments)} rows → {COMMENTS_FILE}")
        print(f"  Courses:  {len(all_courses)} rows → {COURSES_FILE}")
        if errors:
            print(f"  Errors:   {len(errors)} reports failed")
            for e in errors[:10]:
                print(f"    - {e[:80]}")

        input("\nPress Enter to close browser...")
        browser.close()


if __name__ == "__main__":
    main()