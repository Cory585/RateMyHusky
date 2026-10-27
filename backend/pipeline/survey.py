"""The 'survey' source: accepted student ratings as summary rows, and approved new courses.

Same three row kinds as summaries.py: professor (slug, ''), course ('', code),
professor x course (slug, code). Rating and hours are means rounded to two
places (hours from each bucket's midpoint); the rating distribution always has
keys "1"-"5"; grades count only when given. Difficulty is the mean of the
ratings that answered it (older ones didn't); would-take-again is not asked,
so it stays None. A rating with no course counts toward the professor row only.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scraper"))
from load_catalog import normalize_text  # noqa: E402

HOURS_MIDPOINTS = {"0-2": 1.0, "3-4": 3.5, "5-7": 6.0, "8-10": 9.0, "10+": 12.0}


def _mean(values):
    return round(sum(values) / len(values), 2) if values else None


def _row(slug, code, ratings):
    grades = {}
    for r in ratings:
        if r["grade"]:
            grades[r["grade"]] = grades.get(r["grade"], 0) + 1
    return {
        "professor_slug": slug, "course_code": code, "source": "survey",
        "rating": _mean([r["rating"] for r in ratings]),
        "difficulty": _mean([r["difficulty"] for r in ratings if r["difficulty"] is not None]),
        "would_take_again_pct": None,
        "num_ratings": len(ratings),
        "num_comments": sum(1 for r in ratings if (r["comment"] or "").strip()),
        "hours_per_week": _mean([HOURS_MIDPOINTS[r["hours"]] for r in ratings if r["hours"] in HOURS_MIDPOINTS]),
        "rating_distribution": {str(s): sum(1 for r in ratings if r["rating"] == s) for s in range(1, 6)},
        "grade_distribution": grades,
    }


def survey_rows(ratings):
    """Summary rows for accepted ratings with a professor. Pure."""
    by_slug, by_code, by_pair = {}, {}, {}
    for r in ratings:
        by_slug.setdefault(r["professor_slug"], []).append(r)
        if r["course_code"]:
            by_code.setdefault(r["course_code"], []).append(r)
            by_pair.setdefault((r["professor_slug"], r["course_code"]), []).append(r)
    rows = [_row(slug, "", rs) for slug, rs in sorted(by_slug.items())]
    rows += [_row("", code, rs) for code, rs in sorted(by_code.items())]
    rows += [_row(slug, code, rs) for (slug, code), rs in sorted(by_pair.items())]
    return rows


def approved_courses(approved, catalog_courses):
    """catalog_courses-shaped rows for approved codes the catalog doesn't have yet. Pure."""
    known = {c["code"] for c in catalog_courses}
    out = []
    for a in approved:
        if a["code"] in known:
            continue
        known.add(a["code"])
        out.append({
            "code": a["code"], "name": a["name"], "department": None,
            "subject": re.match(r"[A-Za-z]*", a["code"]).group(0),
            "search_text": " ".join(filter(None, [normalize_text(a["code"]), normalize_text(a["name"])])),
            "is_placeholder": False,
        })
    return out
