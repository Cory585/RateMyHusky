"""Injectable SQL orchestration for the unauthenticated /full professor page.

Pure functions that take `query`/`query_one` (the chat_retrieve.py pattern) so
they can be unit-tested without a live DB. server.py wires these to the real
query helpers. This collapses the old cold-path round-trips:

  1x professors_catalog, 1x rmp_reviews course aggregate, 1x rmp_reviews,
  1x reddit  (~4 sequential trips)
"""

import moderation
import rmp
from prof_aliases import ALIAS_MAP


def _resolve_professor(slug, query_one):
    """One catalog lookup, slug then name_key fallback. Returns the row or None."""
    prof = query_one("SELECT * FROM professors_catalog WHERE slug = %s", (slug,))
    if not prof:
        name_key = slug.strip().lower().replace("-", " ")
        name_key = ALIAS_MAP.get(name_key, name_key)
        prof = query_one("SELECT * FROM professors_catalog WHERE name_key = %s", (name_key,))
    return prof


def build_courses(name_key, query):
    """The professor's course list, aggregated from rmp_reviews.course_code.

    Names come from course_catalog (NULL when the course has no known name).
    Sorted by review count, most-reviewed first.
    """
    rows = query(f"""
        SELECT rr.course_code AS code, cc.name AS name,
               COUNT(*) AS num_reviews,
               AVG(rr.quality) AS avg_rating,
               AVG(rr.difficulty) AS avg_difficulty,
               MAX(rr.date) AS latest_date
        FROM rmp_reviews rr
        LEFT JOIN course_catalog cc ON cc.code = rr.course_code
        WHERE rr.name_key = %s AND rr.course_code IS NOT NULL{moderation.sql_filter("rr")}
        GROUP BY rr.course_code, cc.name
        ORDER BY num_reviews DESC, rr.course_code
    """, (name_key,))
    return [{
        "code": r["code"],
        "name": r["name"] or None,
        "numReviews": int(r["num_reviews"]),
        "avgRating": round(float(r["avg_rating"]), 2) if r["avg_rating"] is not None else None,
        "avgDifficulty": round(float(r["avg_difficulty"]), 2) if r["avg_difficulty"] is not None else None,
        "latestDate": str(r["latest_date"]) if r["latest_date"] else None,
    } for r in rows]


def build_profile_unauthed(prof, query):
    """Build the unauthenticated profile dict from an already-fetched catalog
    row plus the professor's rmp_reviews course list.
    """
    return {
        "name": prof["name"],
        "department": prof["department"],
        "rmpRating": round(prof["rmp_rating"], 2) if prof["rmp_rating"] else None,
        # None, not 0.0: avg_rating is NULL for a professor with no RMP ratings,
        # and 0 is not a rating — the scale starts at 1. Matches every other
        # producer of this field (server.py, bookmarks.py).
        "avgRating": round(prof["avg_rating"], 2) if prof["avg_rating"] else None,
        "wouldTakeAgainPct": round(prof["would_take_again_pct"], 1) if prof["would_take_again_pct"] else None,
        "difficulty": round(prof["difficulty"], 2) if prof["difficulty"] else None,
        "totalRatings": prof["total_reviews"],
        "professorUrl": prof["professor_url"],
        "imageUrl": prof["image_url"],
        "focusX": prof.get("focus_x") if prof.get("focus_x") is not None else 50.0,
        "focusY": prof.get("focus_y") if prof.get("focus_y") is not None else 30.0,
        "courses": build_courses(prof["name_key"], query),
    }


def build_reviews(slug, prof, query, sanitize, fetch_reddit_mentions):
    """Build reviews/redditMentions for the professor."""
    reviews = rmp.fetch_reviews(prof["name_key"], query, sanitize)
    reddit_mentions = fetch_reddit_mentions(slug, query, moderation.sql_filter("t"))
    for m in reddit_mentions:
        m["body"] = sanitize(m["body"]) if m["body"] else ""

    return {"reviews": reviews, "redditMentions": reddit_mentions}


def _build_payload(slug, query, query_one, sanitize, fetch_reddit_mentions):
    """(catalog row, v1 payload), or (None, None) when the professor is unknown."""
    if fetch_reddit_mentions is None:
        def fetch_reddit_mentions(_slug, _q, _mod_filter=""):
            return []

    prof = _resolve_professor(slug, query_one)
    if not prof:
        return None, None

    profile = build_profile_unauthed(prof, query)
    reviews = build_reviews(slug, prof, query, sanitize, fetch_reddit_mentions)

    profile["reviews"] = reviews["reviews"]
    profile["redditMentions"] = reviews["redditMentions"]
    return prof, profile


def build_full(slug, query, query_one, sanitize,
               fetch_reddit_mentions=None):
    """Orchestrate the /full payload with shared lookups.

    Returns the combined profile+reviews dict, or None if the professor does
    not exist (caller maps None to a 404).
    """
    _, profile = _build_payload(slug, query, query_one, sanitize,
                                fetch_reddit_mentions)
    return profile


# ── v2: RMP in its own section ───────────────────────────────────────────────
#
# v1 hands the page RMP numbers flat on the profile. v2 serves who the
# professor is as `identity` and what RMP reported as `sources.rmp`; the
# per-course list and Reddit mentions pass through untouched.

# v1 fields that are RMP data now under sources.rmp, or identity now under
# `identity`. colleagues is dropped too: the page does not render it (render.py
# reads v1).
_V1_ONLY = frozenset({
    "name", "department", "imageUrl", "focusX", "focusY",
    "avgRating", "totalRatings", "totalComments", "difficulty",
    "rmpRating", "rmpNumRatings", "rmpDifficulty", "wouldTakeAgainPct",
    "professorUrl", "reviews", "colleagues",
})


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


def to_v2(payload, prof, rmp_section):
    """Reshape a v1 payload (either path's) into v2. Pure, so both the
    unauthenticated builder and server.py's authenticated branch share it."""
    rest = {k: v for k, v in payload.items() if k not in _V1_ONLY}
    return {
        "version": 2,
        "identity": build_identity(prof),
        "sources": {"rmp": rmp_section},
        **rest,
    }


def build_full_v2(slug, query, query_one, sanitize, fetch_rmp_links,
                  fetch_reddit_mentions=None):
    """The v2 /full payload, or None for a 404.

    `fetch_rmp_links(slug)` returns rmp.fetch_link_rows' rows, or None when the
    RMP tables are not built yet (server.py catches the missing table). One
    round-trip on top of v1: the reviews it summarises are the ones v1 already
    fetched, not a second rmp_reviews scan.
    """
    prof, payload = _build_payload(slug, query, query_one, sanitize,
                                   fetch_reddit_mentions)
    if prof is None:
        return None
    section = rmp.build_section(prof, fetch_rmp_links(prof["slug"]), payload["reviews"])
    return to_v2(payload, prof, section)
