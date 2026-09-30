"""Unit tests for the extracted, injectable /full SQL orchestration.

These prove the round-trip reduction (a fake `query` records every call) and
guard the unauthenticated response shape. server.py can't be imported in tests
(it needs live env vars), so the logic lives in professor_full.py with the same
injectable-`query` pattern as chat_retrieve.py.
"""

from professor_full import build_full, _resolve_professor


class RecordingQuery:
    """Fake query()/query_one() that records each SQL it is asked to run and
    returns canned rows based on which table the SQL targets."""

    def __init__(self, catalog=None):
        self.calls = []  # list of SQL strings, in order
        self.catalog = catalog or {}  # fields to override on the catalog row

    def _rows_for(self, sql):
        s = sql.lower()
        if "from professors_catalog" in s:
            return [{"name": "Olin Guha", "slug": "olin-guha", "name_key": "olin guha",
                     "department": "Khoury", "rmp_rating": 4.1,
                     "avg_rating": 4.1, "difficulty": 3.5, "would_take_again_pct": 88.0,
                     "total_reviews": 31, "professor_url": None, "image_url": None,
                     **self.catalog}]
        if "group by rr.course_code" in s:
            return [
                {"code": "CS3500", "name": "Object-Oriented Design", "num_reviews": 5,
                 "avg_rating": 4.4, "avg_difficulty": 3.6, "latest_date": "2024-03-01"},
                {"code": "CS2510", "name": None, "num_reviews": 2,
                 "avg_rating": None, "avg_difficulty": None, "latest_date": None},
            ]
        if "from rmp_reviews" in s:
            return [{"course": "CS3500", "quality": 5, "difficulty": 3, "date": "2024",
                     "tags": "", "attendance": "", "grade": "A", "textbook": "",
                     "online_class": "", "comment": "Great teacher."}]
        if "from reddit_mentions" in s:
            return [{"body": "guha is hard", "subreddit": "NEU", "permalink": "/r/x",
                     "created_utc": None, "reddit_score": 12, "sentiment": "negative",
                     "sentiment_score": -0.4}]
        return []

    def query(self, sql, params=None):
        self.calls.append(sql)
        return self._rows_for(sql)

    def query_one(self, sql, params=None):
        self.calls.append(sql)
        rows = self._rows_for(sql)
        return rows[0] if rows else None

    # ── assertion helpers ──
    def count_hitting(self, table):
        return sum(1 for c in self.calls if table.lower() in c.lower())


def _fake_fetch_reddit_mentions(slug, query_fn, mod_filter=""):
    # Mirror server's fetch_reddit_mentions: a real round-trip through query(),
    # moderation predicate included, so the round-trip count stays honest.
    rows = query_fn("SELECT t.body, t.subreddit FROM reddit_mentions m "
                    "JOIN reddit_text t ON t.source_id = m.source_id "
                    f"WHERE m.professor_slug = %s{mod_filter}", (slug,))
    return [{"body": r.get("body") or "", "sentiment": r.get("sentiment"),
             "sentiment_score": r.get("sentiment_score"), "score": r.get("reddit_score"),
             "subreddit": r.get("subreddit"), "permalink": r.get("permalink"),
             "created_utc": r.get("created_utc")} for r in rows]


def _build(slug="olin-guha", catalog=None):
    rq = RecordingQuery(catalog)
    data = build_full(slug, rq.query, rq.query_one, sanitize=lambda t: t,
                      fetch_reddit_mentions=_fake_fetch_reddit_mentions)
    return data, rq


# ── Round-trip reduction (the whole point) ──

def test_full_makes_at_most_four_round_trips():
    _, rq = _build()
    assert len(rq.calls) <= 4, f"expected <=4 round-trips, got {len(rq.calls)}: {rq.calls}"


def test_catalog_looked_up_only_once():
    # professor_profile + professor_reviews used to each fetch the catalog row.
    _, rq = _build()
    assert rq.count_hitting("from professors_catalog") == 1


def test_no_query_touches_removed_tables():
    _, rq = _build()
    assert not any("trace" in c.lower() for c in rq.calls)


# ── Response shape is preserved ──

def test_full_returns_profile_fields():
    data, _ = _build()
    assert data["name"] == "Olin Guha"
    assert data["department"] == "Khoury"
    assert data["avgRating"] == 4.1
    assert data["totalRatings"] == 31
    assert data["wouldTakeAgainPct"] == 88.0


def test_unrated_professor_serves_null_avg_rating_rather_than_zero():
    """NULL avg_rating must stay null across the wire, not become 0.0.

    precompute writes NULL for a professor with no RMP ratings, who still
    renders a stats card. Coalescing to 0.0 made that card read "0.00" under
    five empty stars, while Total Ratings beside it read "—", because that one treats 0 as absent. 0 is not a rating: the scale
    starts at 1. Every other producer of this field (server.py's leaderboard and
    catalog rows, bookmarks.py) already serves None; this was the odd one out.
    """
    data, _ = _build(catalog={"avg_rating": None, "rmp_rating": None,
                              "total_reviews": 0})
    assert data["avgRating"] is None


def test_full_includes_reviews_and_reddit():
    data, _ = _build()
    assert "reviews" in data and "redditMentions" in data
    assert "traceComments" not in data
    assert data["reviews"][0]["course"] == "CS3500"
    assert data["redditMentions"][0]["sentiment"] == "negative"


def test_full_serves_courses_from_rmp_reviews():
    data, _ = _build()
    assert data["courses"] == [
        {"code": "CS3500", "name": "Object-Oriented Design", "numReviews": 5,
         "avgRating": 4.4, "avgDifficulty": 3.6, "latestDate": "2024-03-01"},
        {"code": "CS2510", "name": None, "numReviews": 2,
         "avgRating": None, "avgDifficulty": None, "latestDate": None},
    ]


def test_full_difficulty_is_rmp_only_and_drops_removed_fields():
    data, _ = _build()
    assert data["difficulty"] == 3.5
    for gone in ("traceRating", "traceCourses", "traceRatingCounts", "hoursPerWeek"):
        assert gone not in data


def test_full_404_when_professor_missing():
    rq = RecordingQuery()
    rq._rows_for = lambda sql: []  # nothing found
    result = build_full("nobody", rq.query, rq.query_one, sanitize=lambda t: t)
    assert result is None  # caller turns None into a 404


def test_resolve_professor_applies_alias_map_to_slug_fallback():
    # /professor/chris-bosso was live before "Chris Bosso" -> "Christopher Bosso"
    # was added to ALIAS_MAP; the slug->name_key fallback must apply the alias so
    # the old link still resolves instead of 404ing on the stale "chris bosso" key.
    calls = []

    def fake_query_one(sql, params=None):
        calls.append(params)
        if "where slug" in sql.lower():
            return None  # force the name_key fallback
        return {"name_key": "christopher bosso"}

    _resolve_professor("chris-bosso", fake_query_one)
    assert calls[-1] == ("christopher bosso",), calls


# ── v2: RMP in its own section ──────────────────────────────────────────────
# The same fake DB as above: RMP rating 4.1, difficulty 3.5, total_reviews 31.

from professor_full import build_full_v2  # noqa: E402

RMP_LINK = {"rmp_legacy_id": 999, "match_method": "exact", "needs_review": False,
            "link_url": "https://www.ratemyprofessors.com/professor/999",
            "rating": 4.1, "difficulty": 3.5, "would_take_again_pct": 88.0,
            "num_ratings": 1, "professor_url": "https://www.ratemyprofessors.com/professor/999",
            "scraped_at": None}


def _build_v2(fetch_rmp_links=None, catalog=None):
    rq = RecordingQuery(catalog)
    if fetch_rmp_links is None:
        def fetch_rmp_links(slug):
            # A real round-trip, so the count below stays honest.
            rq.query("SELECT 1 FROM prof_rmp_link l LEFT JOIN prof_rmp_summary s "
                     "ON s.slug = l.slug WHERE l.slug = %s", (slug,))
            return [RMP_LINK]
    data = build_full_v2("olin-guha", rq.query, rq.query_one, sanitize=lambda t: t,
                         fetch_rmp_links=fetch_rmp_links,
                         fetch_reddit_mentions=_fake_fetch_reddit_mentions)
    return data, rq


def test_v2_splits_identity_from_rmp():
    data, _ = _build_v2()
    assert data["version"] == 2
    assert data["identity"] == {"slug": "olin-guha", "name": "Olin Guha",
                                "department": "Khoury", "college": None,
                                "imageUrl": None, "focusX": 50.0, "focusY": 30.0}
    assert data["sources"]["rmp"]["rating"] == 4.1
    assert data["sources"]["rmp"]["reviews"][0]["course"] == "CS3500"


def test_v2_serves_exactly_the_contract_keys():
    data, _ = _build_v2()
    assert set(data) == {"version", "identity", "sources", "courses", "redditMentions"}
    assert set(data["sources"]) == {"rmp"}
    assert data["courses"][0]["code"] == "CS3500"
    assert data["redditMentions"][0]["sentiment"] == "negative"


def test_v2_moves_v1_rmp_fields_under_sources():
    data, _ = _build_v2()
    for moved in ("avgRating", "totalRatings", "totalComments", "difficulty",
                  "rmpRating", "wouldTakeAgainPct", "professorUrl", "reviews",
                  "colleagues", "name", "department", "imageUrl"):
        assert moved not in data


def test_v2_costs_one_round_trip_more_than_v1():
    """The RMP link/summary lookup. Reviews are v1's, not fetched twice."""
    _, rq = _build_v2()
    assert len(rq.calls) <= 5, rq.calls
    assert rq.count_hitting("select course, quality") == 1


def test_v2_without_an_rmp_record_says_so():
    data, _ = _build_v2(fetch_rmp_links=lambda slug: [])
    rmp_section = data["sources"]["rmp"]
    assert rmp_section["available"] is False
    assert rmp_section["reason"] == "no_rmp_record"
    assert rmp_section["rating"] is None


def test_v2_before_the_rmp_tables_exist_uses_the_catalogs_rmp_columns():
    data, _ = _build_v2(fetch_rmp_links=lambda slug: None, catalog={"num_ratings": 12})
    assert data["sources"]["rmp"]["rating"] == 4.1
    assert data["sources"]["rmp"]["numRatings"] == 12     # num_ratings, not total_reviews 31
    assert data["sources"]["rmp"]["difficulty"] == 3.5


def test_v2_404_when_professor_missing():
    rq = RecordingQuery()
    rq._rows_for = lambda sql: []
    assert build_full_v2("nobody", rq.query, rq.query_one, sanitize=lambda t: t,
                         fetch_rmp_links=lambda slug: []) is None


def test_v1_is_unchanged_by_v2():
    """v1 is still the default response and keeps its flat RMP fields."""
    data, _ = _build()
    assert data["difficulty"] == 3.5
    assert data["avgRating"] == 4.1


def test_courses_aggregate_applies_the_moderation_filter(monkeypatch):
    from professor_full import build_courses
    seen = []
    monkeypatch.setenv("MODERATION_ENFORCE", "true")
    build_courses("olin guha", lambda sql, params=None: seen.append(sql) or [])
    assert "rr.mod_action" in seen[0]
    monkeypatch.setenv("MODERATION_ENFORCE", "false")
    seen.clear()
    build_courses("olin guha", lambda sql, params=None: seen.append(sql) or [])
    assert "mod_action" not in seen[0]
