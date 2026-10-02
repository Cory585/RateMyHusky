"""The course page payload: /api/courses/<code> and render.py.

Injectable `query`/`query_one` like professor_full.py. Two statements: the
course_catalog row, then this course's RMP ratings grouped by professor.
`summary` is the blend (rating-count-weighted over the professors); a new
source adds `summary.bySource.<name>`.
"""

import moderation


def _round(value, places):
    return round(float(value), places) if value is not None else None


def _weighted(rows, key):
    pairs = [(r[key], r["num_ratings"]) for r in rows if r[key] is not None and r["num_ratings"]]
    total = sum(n for _, n in pairs)
    return round(sum(v * n for v, n in pairs) / total, 2) if total else None


def build_course(code, query, query_one):
    """The §4.3 course payload for a normalized code, or None when the code is
    not in course_catalog. A catalog course with no ratings is still a page."""
    course = query_one("SELECT code, name, department FROM course_catalog WHERE code = %s", (code,))
    if not course:
        return None
    rows = query(f"""
        SELECT p.slug, p.name, p.image_url, p.focus_x, p.focus_y,
               AVG(CASE WHEN CAST(r.quality AS FLOAT) BETWEEN 1 AND 5
                        THEN CAST(r.quality AS FLOAT) END) AS rating,
               AVG(CASE WHEN CAST(r.difficulty AS FLOAT) BETWEEN 1 AND 5
                        THEN CAST(r.difficulty AS FLOAT) END) AS difficulty,
               COUNT(*) AS num_ratings
        FROM rmp_reviews r
        JOIN professors_catalog p ON p.name_key = r.name_key
        WHERE UPPER(REPLACE(r.course, ' ', '')) = %s{moderation.sql_filter("r")}
        GROUP BY p.slug, p.name, p.image_url, p.focus_x, p.focus_y
        ORDER BY num_ratings DESC, p.name
    """, (code,))
    rating = _weighted(rows, "rating")
    num_ratings = sum(int(r["num_ratings"]) for r in rows)
    return {
        "code": course["code"],
        "name": course["name"],
        "department": course["department"] or "",
        "catalog": None,
        "summary": {
            "rating": rating,
            "difficulty": _weighted(rows, "difficulty"),
            "numRatings": num_ratings,
            "hoursPerWeek": None,
            "bySource": {"rmp": {"rating": rating, "numRatings": num_ratings}},
        },
        "professors": [{
            "slug": r["slug"],
            "name": r["name"],
            "imageUrl": r["image_url"],
            "focusX": r["focus_x"] if r["focus_x"] is not None else 50.0,
            "focusY": r["focus_y"] if r["focus_y"] is not None else 30.0,
            "rating": _round(r["rating"], 2),
            "difficulty": _round(r["difficulty"], 2),
            "numRatings": int(r["num_ratings"]),
        } for r in rows],
    }
