"""The professor page payload: /api/professors/<slug>/full and render.py.

Injectable `query`/`query_one` (the chat_retrieve.py pattern) so it is
unit-tested without a database. At most five statements: the catalog row, the
professor's source_summary rows, RMP reviews, Reddit, Banner teaching history.

`summary` is the only place blended numbers live; `sources.<name>` holds what
each source measured. A new source adds `sources.<name>` and
`summary.bySource.<name>` and nothing else.
"""

import logging

import moderation
import rmp
import teaching_awards
import teaching_history
from prof_aliases import ALIAS_MAP

log = logging.getLogger(__name__)


def _resolve_professor(slug, query_one):
    """One catalog lookup, slug then name_key fallback (alias-resolved)."""
    prof = query_one("SELECT * FROM professors_catalog WHERE slug = %s", (slug,))
    if not prof:
        name_key = slug.strip().lower().replace("-", " ")
        name_key = ALIAS_MAP.get(name_key, name_key)
        prof = query_one("SELECT * FROM professors_catalog WHERE name_key = %s", (name_key,))
    return prof


def build_identity(prof):
    return {
        "slug": prof["slug"],
        "name": prof["name"],
        "department": prof["department"],
        "college": prof.get("college"),
        "imageUrl": prof.get("image_url"),
        "focusX": prof.get("focus_x") if prof.get("focus_x") is not None else 50.0,
        "focusY": prof.get("focus_y") if prof.get("focus_y") is not None else 30.0,
    }


def build_summary(own_rows, reddit_mentions):
    """`own_rows`: {source: professor-level source_summary row}."""
    b = own_rows.get("blend") or {}
    return {
        "rating": rmp.stat(b.get("rating"), 2),
        "difficulty": rmp.stat(b.get("difficulty"), 2),
        "wouldTakeAgainPct": rmp.pct(b.get("would_take_again_pct")),
        "numRatings": int(b.get("num_ratings") or 0),
        "numComments": int(b.get("num_comments") or 0) + len(reddit_mentions),
        "hoursPerWeek": rmp.stat(b.get("hours_per_week"), 1),
        "bySource": {src: {"rating": rmp.stat(r.get("rating"), 2), "numRatings": int(r.get("num_ratings") or 0)}
                     for src, r in own_rows.items() if src != "blend"},
    }


def build_courses(course_rows, history):
    """Reviewed courses (the blend rows) merged on code with the courses Banner
    has them teaching. Taught courses come first in `history`'s order (most
    recently taught, then most terms); the rest follow most-rated first. A
    course Banner lists but no one has rated has no ratings. hasPage: the code
    is in course_catalog, so a course page exists."""
    reviewed = {r["course_code"]: {
        "code": r["course_code"],
        "name": r["course_name"],
        "rating": rmp.stat(r["rating"], 2),
        "difficulty": rmp.stat(r["difficulty"], 2),
        "numRatings": int(r["num_ratings"] or 0),
        "ratingDistribution": rmp.distribution(r["rating_distribution"]),
        "hasPage": r["course_name"] is not None,
    } for r in course_rows}
    taught = []
    seen = set()
    for h in history:
        if h["code"] in seen:   # a second Banner instructor key: the newest row wins
            continue
        seen.add(h["code"])
        row = reviewed.pop(h["code"], None) or {
            "code": h["code"], "name": h["name"], "rating": None, "difficulty": None,
            "numRatings": 0, "ratingDistribution": None, "hasPage": h["hasPage"]}
        taught.append({**row, "name": row["name"] or h["name"],   # Banner's title when the catalog has none
                       "terms": h["recentTerms"], "lastTerm": h["lastTerm"]})
    rest = sorted(reviewed.values(), key=lambda c: (-c["numRatings"], c["code"]))
    return taught + [{**c, "terms": [], "lastTerm": None} for c in rest]


def _course_history(slug, query, course_history_for):
    """(courses, failed): Banner's courses for the professor, or [] when they
    can't be read: history decorates the page and never fails it, and failed
    True keeps the caller from caching the page."""
    try:
        return course_history_for(slug, query), False
    except Exception:
        log.exception("teaching history failed for professor %s", slug)
        return [], True


def build_payload(slug, query, query_one, sanitize, fetch_reddit_mentions,
                  awards_for=teaching_awards.awards_for,
                  course_history_for=teaching_history.professor_course_history):
    """The §4.2 professor payload, or None when no professor matches `slug`.
    Five statements: catalog row, summaries, RMP reviews, Reddit, then Banner
    history last, so a failure there can't affect the reads before it. Awards
    come from the committed teaching_awards.json, not the database."""
    prof = _resolve_professor(slug, query_one)
    if not prof:
        return None
    summaries = query("""
        SELECT s.course_code, s.source, s.rating, s.difficulty, s.would_take_again_pct,
               s.num_ratings, s.num_comments, s.hours_per_week,
               s.rating_distribution, s.grade_distribution, c.name AS course_name
        FROM source_summary s
        LEFT JOIN course_catalog c ON c.code = s.course_code
        WHERE s.professor_slug = %s
    """, (prof["slug"],))
    own = {r["source"]: r for r in summaries if r["course_code"] == ""}
    reviews = rmp.fetch_reviews(prof["name_key"], query, sanitize)
    mentions = fetch_reddit_mentions(prof["slug"], query, moderation.sql_filter("t"))
    for m in mentions:
        m["body"] = sanitize(m["body"]) if m["body"] else ""
    history, history_failed = _course_history(prof["slug"], query, course_history_for)
    payload = {
        "version": 2,
        "identity": build_identity(prof),
        "summary": build_summary(own, mentions),
        "sources": {"rmp": rmp.build_section(own.get("rmp"), prof.get("professor_url"), reviews)},
        "courses": build_courses([r for r in summaries if r["course_code"] and r["source"] == "blend"],
                                 history),
        "lastTaught": history[0]["lastTerm"] if history else None,
        "redditMentions": mentions,
        "awards": awards_for(prof["name_key"]),
    }
    if history_failed:
        payload["_historyFailed"] = True    # private: server.professor_payload pops it
    return payload
