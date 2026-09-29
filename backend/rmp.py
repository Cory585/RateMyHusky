"""Rate My Professors, read on its own.

Everything the professor page shows from RMP comes through here, and every
number this module returns was measured by RMP: it reads prof_rmp_link,
prof_rmp_summary and rmp_reviews, and nothing TRACE- or Reddit-derived. The
page used to receive RMP difficulty already averaged with TRACE's (and render
the average as "Difficulty"); keeping RMP in its own module and its own section
of the response is what stops that from happening again as pages are added.

Injectable `query` like professor_full.py, so tests run without a database.
"""

import moderation

# Below this many ratings the section says so beside the number: a 5.0 from two
# ratings reads as authoritative as a 5.0 from two hundred.
FEW_RATINGS = 5
TOP_TAGS = 8

# Grades a reviewer can pick that are not a grade.
_NOT_A_GRADE = {"", "N/A", "Not sure yet", "Rather not say"}

# Weakest first: a professor linked through several RMP pages reports the
# least certain way any of them was matched.
_METHOD_RANK = {"fuzzy": 0, "manual": 1, "alias": 2, "exact": 3}


def fetch_reviews(name_key, query, sanitize):
    """The professor's stored RMP ratings, moderation filter applied."""
    rows = query(f"""
        SELECT course, quality, difficulty, date, tags, attendance, grade,
               textbook, online_class, comment
        FROM rmp_reviews WHERE name_key = %s{moderation.sql_filter()}
    """, (name_key,))
    return [{
        "course": str(r["course"] or ""),
        "quality": int(r["quality"]) if r["quality"] else 0,
        "difficulty": int(r["difficulty"]) if r["difficulty"] else 0,
        "date": str(r["date"] or ""),
        "tags": str(r["tags"] or ""),
        "attendance": str(r["attendance"] or ""),
        "grade": str(r["grade"] or ""),
        "textbook": str(r["textbook"] or ""),
        "online_class": str(r["online_class"] or ""),
        "comment": sanitize(r["comment"]) if r["comment"] else "",
    } for r in rows]


def fetch_link_rows(slug, query):
    """One row per RMP page linked to `slug`, each carrying the summary.

    One round-trip: every link row repeats the professor's single summary row.
    Raises if the tables do not exist yet; server.py turns that into None, which
    build_section reads as "fall back to the catalog's RMP columns".
    """
    return query("""
        SELECT l.rmp_legacy_id, l.match_method, l.needs_review,
               l.professor_url AS link_url,
               s.rating, s.difficulty, s.would_take_again_pct, s.num_ratings,
               s.professor_url, s.scraped_at
        FROM prof_rmp_link l
        LEFT JOIN prof_rmp_summary s ON s.slug = l.slug
        WHERE l.slug = %s
    """, (slug,))


def rating_distribution(reviews):
    counts = {str(s): 0 for s in range(1, 6)}
    for r in reviews:
        q = r.get("quality") or 0
        if 1 <= q <= 5:
            counts[str(int(round(q)))] += 1
    return counts


def grade_distribution(reviews):
    counts = {}
    for r in reviews:
        g = (r.get("grade") or "").strip()
        if g not in _NOT_A_GRADE:
            counts[g] = counts.get(g, 0) + 1
    return counts


def top_tags(reviews, limit=TOP_TAGS):
    """Most-used RMP tags. RMP joins a review's tags with "--"."""
    counts = {}
    for r in reviews:
        for t in (r.get("tags") or "").split("--"):
            t = t.strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [{"tag": t, "count": n} for t, n in ranked[:limit]]


def _iso(ts):
    return ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else None)


def _summary_from_links(rows):
    s = rows[0]
    methods = sorted({r["match_method"] for r in rows if r.get("match_method")},
                     key=lambda m: _METHOD_RANK.get(m, -1))
    return {
        "rating": s.get("rating"),
        "difficulty": s.get("difficulty"),
        "wouldTakeAgainPct": s.get("would_take_again_pct"),
        "numRatings": int(s.get("num_ratings") or 0),
        "professorUrl": s.get("professor_url") or s.get("link_url"),
        "scrapedAt": _iso(s.get("scraped_at")),
        "matchMethod": methods[0] if methods else None,
        "rmpPages": len(rows),
    }


def _summary_from_catalog(prof):
    """professors_catalog's RMP-only columns, for a DB without the RMP tables.

    rmp_rating, num_ratings, difficulty, would_take_again_pct and professor_url
    are all written from RMP alone; the blends live in avg_rating,
    total_reviews and the API. No scrape date or match method is recorded
    there, so those stay None rather than guessed.
    """
    has_record = bool(prof.get("professor_url")) or (prof.get("num_ratings") or 0) > 0
    if not has_record:
        return None
    return {
        "rating": prof.get("rmp_rating"),
        "difficulty": prof.get("difficulty"),
        "wouldTakeAgainPct": prof.get("would_take_again_pct"),
        "numRatings": int(prof.get("num_ratings") or 0),
        "professorUrl": prof.get("professor_url"),
        "scrapedAt": None,
        "matchMethod": None,
        "rmpPages": None,
    }


def _round(v, places):
    return round(float(v), places) if v is not None else None


def build_section(prof, link_rows, reviews):
    """The `sources.rmp` block of the v2 professor payload.

    `link_rows` is fetch_link_rows' result: a list (empty = no RMP record), or
    None when the RMP tables are not in the database yet. `reviews` is
    fetch_reviews' result, already fetched by the caller for the reviews list.

    Always the same keys, so the frontend has one shape to handle. A professor
    without RMP data is `available: False` with a reason, never a number
    borrowed from another source.
    """
    summary = (_summary_from_catalog(prof) if link_rows is None
               else _summary_from_links(link_rows) if link_rows else None)

    section = {
        "available": False,
        "reason": "no_rmp_record",
        "rating": None,
        "difficulty": None,
        "wouldTakeAgainPct": None,
        "numRatings": 0,
        "fewRatings": False,
        "ratingDistribution": rating_distribution(reviews),
        "gradeDistribution": grade_distribution(reviews),
        "topTags": top_tags(reviews),
        "reviews": reviews,
        "professorUrl": None,
        "scrapedAt": None,
        "matchMethod": None,
        "rmpPages": None,
    }
    if summary is None:
        return section

    section.update({
        "professorUrl": summary["professorUrl"],
        "scrapedAt": summary["scrapedAt"],
        "matchMethod": summary["matchMethod"],
        "rmpPages": summary["rmpPages"],
        "numRatings": summary["numRatings"],
    })
    rating = summary["rating"]
    if summary["numRatings"] <= 0 or not rating:
        # An RMP page with nothing on it: keep the link, show no number.
        section["reason"] = "no_ratings"
        return section

    section.update({
        "available": True,
        "reason": None,
        "rating": _round(rating, 2),
        "difficulty": _round(summary["difficulty"], 2) if summary["difficulty"] else None,
        "wouldTakeAgainPct": _round(summary["wouldTakeAgainPct"], 1)
        if summary["wouldTakeAgainPct"] is not None else None,
        "fewRatings": summary["numRatings"] < FEW_RATINGS,
    })
    return section
