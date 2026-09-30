"""The professor page payload: /api/professors/<slug>/full and render.py.

Injectable `query`/`query_one` (the chat_retrieve.py pattern) so it is
unit-tested without a database. At most four statements: the catalog row, the
RMP reviews, the names of the courses those reviews name, and Reddit.

`summary` is the only place blended numbers live; `sources.<name>` holds what
each source measured. A new source adds `sources.<name>` and
`summary.bySource.<name>` and nothing else.
"""

import re

import moderation
import rmp
from prof_aliases import ALIAS_MAP

_COURSE_CODE = re.compile(r"^[A-Z]{2,5}\d{4}$")


def _resolve_professor(slug, query_one):
    """One catalog lookup, slug then name_key fallback (alias-resolved)."""
    prof = query_one("SELECT * FROM professors_catalog WHERE slug = %s", (slug,))
    if not prof:
        name_key = slug.strip().lower().replace("-", " ")
        name_key = ALIAS_MAP.get(name_key, name_key)
        prof = query_one("SELECT * FROM professors_catalog WHERE name_key = %s", (name_key,))
    return prof


def normalize_course_code(raw):
    """A review's course field as a catalog code ("cs 2500" -> "CS2500"), or None."""
    code = re.sub(r"\s+", "", str(raw or "")).upper()
    return code if _COURSE_CODE.match(code) else None


def _mean(values):
    return round(sum(values) / len(values), 2) if values else None


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


def build_summary(prof, reviews, reddit_mentions):
    num_ratings = int(prof.get("num_ratings") or 0)
    return {
        "rating": rmp.stat(prof.get("avg_rating"), 2),
        "difficulty": rmp.stat(prof.get("difficulty"), 2),
        "wouldTakeAgainPct": rmp.stat(prof.get("would_take_again_pct"), 1),
        "numRatings": num_ratings,
        "numComments": sum(1 for r in reviews if r["comment"].strip()) + len(reddit_mentions),
        "hoursPerWeek": None,
        "bySource": {"rmp": {"rating": rmp.stat(prof.get("rmp_rating"), 2),
                             "numRatings": num_ratings}},
    }


def build_courses(reviews, names):
    """One row per catalog code the reviews name, most-rated first."""
    groups = {}
    for r in reviews:
        code = normalize_course_code(r["course"])
        if code:
            groups.setdefault(code, []).append(r)
    rows = [{
        "code": code,
        "name": names.get(code),
        "rating": _mean([r["quality"] for r in rs if 1 <= r["quality"] <= 5]),
        "difficulty": _mean([r["difficulty"] for r in rs if 1 <= r["difficulty"] <= 5]),
        "numRatings": len(rs),
        "ratingDistribution": rmp.rating_distribution(rs),
    } for code, rs in groups.items()]
    rows.sort(key=lambda c: (-c["numRatings"], c["code"]))
    return rows


def build_payload(slug, query, query_one, sanitize, fetch_reddit_mentions):
    """The §4.2 professor payload, or None when no professor matches `slug`."""
    prof = _resolve_professor(slug, query_one)
    if not prof:
        return None
    reviews = rmp.fetch_reviews(prof["name_key"], query, sanitize)
    codes = sorted({c for c in (normalize_course_code(r["course"]) for r in reviews) if c})
    names = {}
    if codes:
        names = {r["code"]: r["name"] for r in query(
            "SELECT code, name FROM course_catalog WHERE code = ANY(%s)", (codes,))}
    mentions = fetch_reddit_mentions(prof["slug"], query, moderation.sql_filter("t"))
    for m in mentions:
        m["body"] = sanitize(m["body"]) if m["body"] else ""
    return {
        "version": 2,
        "identity": build_identity(prof),
        "summary": build_summary(prof, reviews, mentions),
        "sources": {"rmp": rmp.build_section(prof, reviews)},
        "courses": build_courses(reviews, names),
        "redditMentions": mentions,
    }
