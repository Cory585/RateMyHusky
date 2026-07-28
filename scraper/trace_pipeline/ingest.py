"""
TRACE Bluera raw-CSV ingest: parsing, term validation, DB-seeded ID resolution.

Usage:  python scraper/trace_pipeline/ingest.py --term "Spring 2026" [--dry-run] [--replace]
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import normalize_name

COURSE_ID_OFFSET, INSTRUCTOR_ID_OFFSET, TERM_ID_OFFSET = 500000, 50000, 900


# ── Row-parsing helpers (ported verbatim from scraper/transform_to_trace.py lines 72-124) ──

def parse_course_info(raw: str):
    """
    Parse strings like:
      '- TRACE report for BIOT5621-01 Protein Principles in Biotech  (Dennis Fernandes)'
      'AACE6000-01 Arts and Culture Leadership  (Diana Arcadipone)'

    Returns (course_code, section, course_name, instructor_name) or None.
    """
    # Strip leading "- TRACE report for " if present
    s = re.sub(r'^-\s*TRACE report for\s*', '', raw.strip())

    # Match: CODE-SECTION  CourseName  (Instructor)
    m = re.match(
        r'([A-Z]{2,10}\d{4})-(\d{1,3})\s+(.+?)\s{2,}\((.+?)\)\s*$',
        s
    )
    if m:
        return m.group(1), m.group(2), m.group(3).strip(), m.group(4).strip()

    # Fallback: try single-space before parens
    m = re.match(
        r'([A-Z]{2,10}\d{4})-(\d{1,3})\s+(.+?)\s+\((.+?)\)\s*$',
        s
    )
    if m:
        return m.group(1), m.group(2), m.group(3).strip(), m.group(4).strip()

    return None


def split_instructor_name(full_name: str):
    """Split 'Dennis Fernandes' into ('Dennis', 'Fernandes')."""
    parts = full_name.strip().split()
    if len(parts) == 0:
        return ("", "")
    if len(parts) == 1:
        return ("", parts[0])
    return (parts[0], " ".join(parts[1:]))


def safe_int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def safe_float(val):
    try:
        v = str(val).replace("%", "").strip()
        return float(v)
    except (ValueError, TypeError):
        return None


# ── Field-name constants (Task 4 consumes exactly these) ──

COURSE_FIELDS = ["courseId", "schoolCode", "termId", "termTitle", "instructorId", "termEndDate",
                 "instructorFirstName", "instructorLastName", "departmentName", "enrollment",
                 "displayName", "section"]
SCORE_FIELDS = ["courseId", "instructorId", "termId", "enrollment", "completed", "question",
                "count_5", "count_4", "count_3", "count_2", "count_1", "mean", "median",
                "std_dev", "dept_mean"]
COMMENT_FIELDS = ["course_url", "question", "comment"]


class IdState:
    """ID maps + high-water marks seeded from the DB. New allocations always continue
    ABOVE the true max — never restart at offset+1 — so a re-run can never collide with
    existing production data (the transform_to_trace counter-reset bug class)."""
    def __init__(self, course_map, instructor_map, term_map, max_course, max_instructor, max_term):
        self._c, self._i, self._t = course_map, instructor_map, term_map
        self._mc, self._mi, self._mt = max_course, max_instructor, max_term
        self.allocated = {"course": 0, "instructor": 0, "term": 0}

    def course_id(self, code_section):
        key = code_section.strip().upper()
        if key not in self._c:
            self._mc += 1
            self._c[key] = self._mc
            self.allocated["course"] += 1
        return self._c[key]

    def instructor_id(self, full_name):
        key = normalize_name(full_name)
        if key not in self._i:
            self._mi += 1
            self._i[key] = self._mi
            self.allocated["instructor"] += 1
        return self._i[key]

    def term_id(self, title):
        key = title.strip()
        if key not in self._t:
            self._mt += 1
            self._t[key] = self._mt
            self.allocated["term"] += 1
        return self._t[key]

    def known_term(self, title):
        return self._t.get(title.strip())


def resolve_ids(query_fn):
    """Seed ID maps from the DB — the single source of truth. Only new-scraper-range rows
    (course>=500001 etc.) seed the maps; counters continue above the true maxes so a re-run
    can NEVER collide with existing data (the transform_to_trace counter-reset bug class)."""
    rows = query_fn("""SELECT course_id, instructor_id, term_id, term_title, display_name,
                              instructor_first_name, instructor_last_name
                       FROM trace_courses""", ())
    course_map, instructor_map, term_map = {}, {}, {}
    mc, mi, mt = COURSE_ID_OFFSET, INSTRUCTOR_ID_OFFSET, TERM_ID_OFFSET
    for r in rows:
        cid, iid, tid = r["course_id"], r["instructor_id"], r["term_id"]
        if cid and cid > COURSE_ID_OFFSET:
            m = re.match(r"^([A-Z0-9]+):(\d+)", str(r.get("display_name") or ""))
            if m:
                course_map.setdefault(f"{m.group(1)}-{m.group(2)}".upper(), cid)
            mc = max(mc, cid)
        if iid and iid > INSTRUCTOR_ID_OFFSET:
            nk = normalize_name(f"{r.get('instructor_first_name') or ''} {r.get('instructor_last_name') or ''}")
            if nk:
                instructor_map.setdefault(nk, iid)
            mi = max(mi, iid)
        if tid and tid > TERM_ID_OFFSET:
            title = (r.get("term_title") or "").strip()
            if title:
                term_map.setdefault(title, tid)
            mt = max(mt, tid)
    return IdState(course_map, instructor_map, term_map, mc, mi, mt)


def resolve_term_title(raw_rows):
    """Return (single term title, blank-term row count). Raises ValueError if the raw CSV
    contains more than one distinct non-empty term title (wrong rid or mixed scrape)."""
    titles = {(r.get("term") or "").strip() for r in raw_rows} - {""}
    blanks = sum(1 for r in raw_rows if not (r.get("term") or "").strip())
    if len(titles) != 1:
        raise ValueError(f"Expected exactly 1 term title in the raw CSV, found {sorted(titles)!r} "
                         "— wrong rid or mixed scrape.")
    return titles.pop(), blanks


def build_prefix_dept_map(query_fn):
    """Course-code prefix -> department name, first-seen wins."""
    rows = query_fn("""SELECT display_name, department_name FROM trace_courses
                       WHERE department_name IS NOT NULL AND department_name <> ''""", ())
    out = {}
    for r in rows:
        m = re.match(r"^([A-Z]+)\d", str(r.get("display_name") or ""))
        if m:
            out.setdefault(m.group(1), r["department_name"])
    return out


def build_rows(raw_rows, ids, prefix_dept_map):
    """Port of transform_to_trace.process_csv (lines 173-337) with five deliberate changes:
    1. Blank-term rows adopt the file's single title (counted in stats["blank_term_rows"])
       instead of being silently skipped.
    2. IDs come from ids.course_id/instructor_id/term_id (never module-level counters).
    3. Client-side dedupe within the build: scores on (courseId, instructorId, termId,
       question) keep-first; comments on (course_url, question, comment) keep-first.
    4. Comment dicts get _tc_course_id/_tc_instructor_id/_tc_term_id keys.
    5. Rows whose course_info fails parse_course_info increment stats["skipped_unparseable"]
       (first 5 samples kept in stats["unparseable_samples"]).
    """
    single_title, _ = resolve_term_title(raw_rows)

    courses = {}   # keyed by (course_id, instructor_id, term_id)
    scores = []
    comments = []
    seen_scores = set()
    seen_comments = set()
    stats = {
        "parsed": 0,
        "skipped_unparseable": 0,
        "blank_term_rows": 0,
        "deduped_scores": 0,
        "deduped_comments": 0,
        "unparseable_samples": [],
    }

    for row in raw_rows:
        course_info = row.get("course_info", "") or row.get("display_name", "")
        if not course_info:
            continue

        parsed = parse_course_info(course_info)
        if not parsed:
            stats["skipped_unparseable"] += 1
            if len(stats["unparseable_samples"]) < 5:
                stats["unparseable_samples"].append(course_info)
            continue
        course_code, section_num, course_name, instructor_name = parsed

        raw_term = (row.get("term") or "").strip()
        if not raw_term:
            stats["blank_term_rows"] += 1
        term = raw_term or single_title

        course_section = f"{course_code}-{section_num}"
        course_id = ids.course_id(course_section)
        instructor_id = ids.instructor_id(instructor_name)
        term_id = ids.term_id(term)

        first_name, last_name = split_instructor_name(instructor_name)
        enrollment = safe_int(row.get("audience", 0))
        completed = safe_int(row.get("Number of Responses") or row.get("responses", 0))

        # Build display_name in existing format: "CS2500:01 (Course Name) - Instructor"
        display_name = f"{course_code}:{section_num} ({course_name}) - {instructor_name}"

        # Register course (dedup by composite key)
        course_key = (course_id, instructor_id, term_id)
        if course_key not in courses:
            prefix_m = re.match(r'^([A-Z]{2,10})\d', course_code or "")
            prefix = prefix_m.group(1) if prefix_m else ""
            courses[course_key] = {
                "courseId": course_id,
                "schoolCode": "SH",
                "termId": term_id,
                "termTitle": term,
                "instructorId": instructor_id,
                "termEndDate": row.get("created_date", ""),
                "instructorFirstName": first_name,
                "instructorLastName": last_name,
                "departmentName": prefix_dept_map.get(prefix, ""),
                "enrollment": enrollment,
                "displayName": display_name,
                "section": section_num,
            }

        stats["parsed"] += 1

        section_type = row.get("section", "").strip()
        question = row.get("question", "").strip()

        # ── Comments rows ──
        if section_type == "Comments":
            comments_json = row.get("comments_json", "").strip()
            comment_prompt = row.get("comment_prompt", "").strip()
            prompt = comment_prompt or question
            if comments_json:
                try:
                    comment_list = json.loads(comments_json)
                    url = f"https://www.applyweb.com/eval/new/coursereport?sp={course_id}&sp={instructor_id}&sp={term_id}"
                    for c in comment_list:
                        c_text = str(c).strip()
                        if not c_text:
                            continue
                        dedupe_key = (url, prompt, c_text)
                        if dedupe_key in seen_comments:
                            stats["deduped_comments"] += 1
                            continue
                        seen_comments.add(dedupe_key)
                        comments.append({
                            "course_url": url,
                            "question": prompt,
                            "comment": c_text,
                            "_tc_course_id": course_id,
                            "_tc_instructor_id": instructor_id,
                            "_tc_term_id": term_id,
                        })
                except (json.JSONDecodeError, TypeError):
                    pass
            continue

        # ── Demographics rows (hours per week only) ──
        if section_type == "Demographics":
            if "hours per week" in question.lower():
                demo_json = row.get("demographics_json", "").strip()
                if demo_json:
                    try:
                        dist = json.loads(demo_json)
                        midpoints = {"0-2": 1, "3-4": 3.5, "5-7": 6, "8-10": 9, "More than 10": 12}
                        total_n = sum(dist.get(k, 0) for k in midpoints)
                        if total_n > 0:
                            mean_val = round(
                                sum(dist.get(k, 0) * v for k, v in midpoints.items()) / total_n, 2
                            )
                            score_key = (course_id, instructor_id, term_id, question)
                            if score_key in seen_scores:
                                stats["deduped_scores"] += 1
                            else:
                                seen_scores.add(score_key)
                                scores.append({
                                    "courseId": course_id,
                                    "instructorId": instructor_id,
                                    "termId": term_id,
                                    "enrollment": enrollment,
                                    "completed": completed,
                                    "question": question,
                                    "count_5": safe_int(dist.get("More than 10", 0)),
                                    "count_4": safe_int(dist.get("8-10", 0)),
                                    "count_3": safe_int(dist.get("5-7", 0)),
                                    "count_2": safe_int(dist.get("3-4", 0)),
                                    "count_1": safe_int(dist.get("0-2", 0)),
                                    "mean": mean_val,
                                    "median": "",
                                    "std_dev": "",
                                    "dept_mean": "",
                                })
                    except (json.JSONDecodeError, TypeError):
                        pass
            continue

        # ── Score rows ──
        if not question:
            continue

        count_5 = safe_int(row.get("count_5", 0))
        count_4 = safe_int(row.get("count_4", 0))
        count_3 = safe_int(row.get("count_3", 0))
        count_2 = safe_int(row.get("count_2", 0))
        count_1 = safe_int(row.get("count_1", 0))
        total = count_1 + count_2 + count_3 + count_4 + count_5

        mean_val = safe_float(row.get("Course Mean"))
        if mean_val is None and total > 0:
            mean_val = round(
                (1*count_1 + 2*count_2 + 3*count_3 + 4*count_4 + 5*count_5) / total,
                2
            )

        median_val = safe_float(row.get("Course Median"))
        dept_mean_val = safe_float(row.get("Dept. Mean"))

        full_question = question
        if "Effectiveness" in section_type:
            full_question = "What is your overall rating of this instructor teaching effectiveness?"

        score_key = (course_id, instructor_id, term_id, full_question)
        if score_key in seen_scores:
            stats["deduped_scores"] += 1
            continue
        seen_scores.add(score_key)
        scores.append({
            "courseId": course_id,
            "instructorId": instructor_id,
            "termId": term_id,
            "enrollment": enrollment,
            "completed": completed,
            "question": full_question,
            "count_5": count_5,
            "count_4": count_4,
            "count_3": count_3,
            "count_2": count_2,
            "count_1": count_1,
            "mean": mean_val if mean_val is not None else "",
            "median": median_val if median_val is not None else "",
            "std_dev": "",
            "dept_mean": dept_mean_val if dept_mean_val is not None else "",
        })

    return {
        "courses": list(courses.values()),
        "scores": scores,
        "comments": comments,
        "term_title": single_title,
        "term_id": ids.term_id(single_title),
        "stats": stats,
    }


def _fake_db_rows():
    # Mix of legacy ApplyWeb rows (ignored for maps) and new-range rows (seed the maps).
    return [
        {"course_id": 12345, "instructor_id": 678, "term_id": 45, "term_title": "Fall 2019",
         "display_name": "CS2500:01 (Fundies) - Old Prof", "instructor_first_name": "Old", "instructor_last_name": "Prof"},
        {"course_id": 500001, "instructor_id": 50001, "term_id": 901, "term_title": "Fall 2025",
         "display_name": "BIOT5621:01 (Protein Principles) - Dennis Fernandes",
         "instructor_first_name": "Dennis", "instructor_last_name": "Fernandes"},
        {"course_id": 504637, "instructor_id": 52396, "term_id": 904, "term_title": "Full Summer 2025",
         "display_name": "CS3500:02 (OOD) - Jane Doe", "instructor_first_name": "Jane", "instructor_last_name": "Doe"},
    ]

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    def q(sql, params=()):
        if "trace_courses" in sql and "department_name" in sql:
            return [{"display_name": "CS3500:02 (OOD) - Jane Doe", "department_name": "Computer Science"}]
        if "trace_courses" in sql:
            return _fake_db_rows()
        return []

    ids = resolve_ids(q)
    # THE collision regression: new allocations continue ABOVE existing maxes, never restart at offset+1.
    check("new course id continues above max", ids.course_id("NEWC1000-01") == 504638)
    check("new instructor id continues above max", ids.instructor_id("Brand New") == 52397)
    check("new term id continues above max", ids.term_id("Spring 2026") == 905)
    # Returning entities reuse their DB ids.
    check("returning course-section reuses id", ids.course_id("CS3500-02") == 504637)
    check("returning instructor reuses id", ids.instructor_id("Jane Doe") == 52396)
    check("instructor key is normalized", ids.instructor_id("  JANE   doe ") == 52396)
    check("known term maps", ids.known_term("Fall 2025") == 901 and ids.known_term("Spring 2026") == 905)
    check("legacy rows do not seed maps", ids.course_id("CS2500-01") == 504639)  # new alloc, not 12345

    # ── resolve_term_title ──
    rows_ok = [{"term": "Spring 2026"}, {"term": ""}, {"term": "Spring 2026"}]
    check("single term resolves + counts blanks", resolve_term_title(rows_ok) == ("Spring 2026", 1))
    try:
        resolve_term_title([{"term": "Spring 2026"}, {"term": "Fall 2025"}])
        check("mixed terms abort", False)
    except ValueError:
        check("mixed terms abort", True)

    # ── build_prefix_dept_map ──
    check("prefix map", build_prefix_dept_map(q) == {"CS": "Computer Science"})

    # ── build_rows on a mini raw file ──
    raw = [
        {"term": "Spring 2026", "created_date": "4/28/2026", "audience": "30",
         "course_info": "CS3500-01 Object-Oriented Design  (Jane Doe)",
         "section": "Course Related Questions", "question": "Materials were organized",
         "Number of Responses": "20", "Course Mean": "", "Course Median": "4.0", "Dept. Mean": "4.1",
         "count_5": "10", "count_4": "10", "count_3": "0", "count_2": "0", "count_1": "0"},
        {"term": "Spring 2026", "course_info": "CS3500-01 Object-Oriented Design  (Jane Doe)",
         "section": "Instructor Effectiveness", "question": "20",
         "Course Mean": "4.7", "count_5": "0", "count_4": "0", "count_3": "0", "count_2": "0", "count_1": "0"},
        {"term": "", "course_info": "CS3500-01 Object-Oriented Design  (Jane Doe)",
         "section": "Comments", "comment_prompt": "Strengths?",
         "comments_json": '["Great lectures; loved it", "Great lectures; loved it", ""]'},
        {"term": "Spring 2026", "course_info": "CS3500-01 Object-Oriented Design  (Jane Doe)",
         "section": "Demographics", "question": "How many hours per week did you spend on this course?",
         "demographics_json": '{"3-4": 12, "5-7": 8}'},
        {"term": "Spring 2026", "course_info": "garbage not parseable", "section": "x", "question": "y"},
    ]
    ids2 = resolve_ids(q)
    built = build_rows(raw, ids2, {"CS": "Computer Science"})
    check("one course row", len(built["courses"]) == 1)
    c = built["courses"][0]
    check("course reuses returning instructor id", c["instructorId"] == 52396)
    check("course display_name format", c["displayName"] == "CS3500:01 (Object-Oriented Design) - Jane Doe")
    check("course dept from prefix map", c["departmentName"] == "Computer Science")
    check("term row uses new term id 905", c["termId"] == 905 and c["termTitle"] == "Spring 2026")
    counted = {s["question"]: s for s in built["scores"]}
    check("counts-based mean computed", counted["Materials were organized"]["mean"] == 4.5)
    check("median + dept_mean carried", counted["Materials were organized"]["median"] == 4.0
          and counted["Materials were organized"]["dept_mean"] == 4.1)
    check("effectiveness question rewritten",
          "What is your overall rating of this instructor teaching effectiveness?" in counted)
    check("effectiveness keeps CSV mean when counts are zero",
          counted["What is your overall rating of this instructor teaching effectiveness?"]["mean"] == 4.7)
    hours = counted["How many hours per week did you spend on this course?"]
    check("hours mean from midpoints", hours["mean"] == round((3.5*12 + 6*8) / 20, 2))
    check("comments deduped + blank dropped (3 json -> 1 row)", len(built["comments"]) == 1)
    cm = built["comments"][0]
    check("comment url embeds this row's ids",
          cm["course_url"] == f"https://www.applyweb.com/eval/new/coursereport?sp={c['courseId']}&sp={c['instructorId']}&sp={c['termId']}")
    check("comment carries tc ids matching url",
          (cm["_tc_course_id"], cm["_tc_instructor_id"], cm["_tc_term_id"]) == (c["courseId"], c["instructorId"], c["termId"]))
    check("blank-term row adopted the single title (comment row came from it)", built["stats"]["blank_term_rows"] == 1)
    check("unparseable counted", built["stats"]["skipped_unparseable"] == 1)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("Import-only module (for now). Use --selftest.")
