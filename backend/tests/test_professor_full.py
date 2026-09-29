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
        # Order matters: trace_comments/reddit before the generic trace_courses.
        if "from professors_catalog" in s:
            return [{"name": "Olin Guha", "slug": "olin-guha", "name_key": "olin guha",
                     "department": "Khoury", "rmp_rating": 4.1, "trace_rating": 4.3,
                     "avg_rating": 4.2, "difficulty": 3.5, "would_take_again_pct": 88.0,
                     "total_reviews": 31, "professor_url": None, "image_url": None,
                     "avg_hours": 6.0, **self.catalog}]
        if "from rmp_reviews" in s:
            return [{"course": "CS3500", "quality": 5, "difficulty": 3, "date": "2024",
                     "tags": "", "attendance": "", "grade": "A", "textbook": "",
                     "online_class": "", "comment": "Great teacher."}]
        if "from trace_comments" in s:
            return [{"tc_term_id": 901, "tc_course_id": 1, "question": "Comments",
                     "comment": "Tough but fair."}]
        if "from reddit_mentions" in s:
            return [{"body": "guha is hard", "subreddit": "NEU", "permalink": "/r/x",
                     "created_utc": None, "reddit_score": 12, "sentiment": "negative",
                     "sentiment_score": -0.4}]
        if "from trace_scores" in s:
            # One overall + one challenge + one hours row for the same course/term.
            base = {"course_id": 1, "term_id": 901, "display_name": "CS3500: OOD"}
            return [
                {**base, "question": "Overall rating", "mean": 4.5,
                 "count_1": 0, "count_2": 0, "count_3": 1, "count_4": 2,
                 "count_5": 7, "completed": 10},
                {**base, "question": "How challenging", "mean": 3.5,
                 "count_1": 0, "count_2": 1, "count_3": 4, "count_4": 3,
                 "count_5": 2, "completed": 10},
                {**base, "question": "Hours per week", "mean": 6.0,
                 "count_1": 1, "count_2": 2, "count_3": 4, "count_4": 2,
                 "count_5": 1, "completed": 10},
            ]
        if "from trace_courses" in s:
            return [{"course_id": 1, "term_id": 901, "term_title": "Fall 2023",
                     "department_name": "Khoury", "display_name": "CS3500: OOD",
                     "section": "1", "enrollment": 40, "instructor_id": 7}]
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
                      fetch_reddit_mentions=_fake_fetch_reddit_mentions,
                      is_authed=False)
    return data, rq


# ── Round-trip reduction (the whole point) ──

def test_full_unauthed_makes_at_most_six_round_trips():
    _, rq = _build()
    assert len(rq.calls) <= 6, f"expected <=6 round-trips, got {len(rq.calls)}: {rq.calls}"


def test_catalog_looked_up_only_once():
    # professor_profile + professor_reviews used to each fetch the catalog row.
    _, rq = _build()
    assert rq.count_hitting("from professors_catalog") == 1


def test_trace_courses_fetched_only_once():
    # Both old functions fetched trace_courses by name_key separately.
    _, rq = _build()
    assert rq.count_hitting("from trace_courses") == 1


def test_trace_scores_scanned_only_once():
    # Old unauthed path ran 3 separate scans (challenge / overall / hours).
    _, rq = _build()
    assert rq.count_hitting("from trace_scores") == 1


# ── Response shape is preserved ──

def test_full_returns_profile_fields():
    data, _ = _build()
    assert data["name"] == "Olin Guha"
    assert data["department"] == "Khoury"
    assert data["avgRating"] == 4.2
    assert data["totalRatings"] == 31
    assert data["wouldTakeAgainPct"] == 88.0


def test_unrated_professor_serves_null_avg_rating_rather_than_zero():
    """NULL avg_rating must stay null across the wire, not become 0.0.

    precompute writes NULL for a professor with no RMP ratings and no responses
    to TRACE's overall question — 2,327 rows of the catalog, ~2,083 of which
    still carry a course list and so render a stats card. Coalescing to 0.0 made
    that card read "0.00" under five empty stars, while Total Ratings beside it
    read "—", because that one treats 0 as absent. 0 is not a rating: the scale
    starts at 1. Every other producer of this field (server.py's leaderboard and
    catalog rows, bookmarks.py) already serves None; this was the odd one out.
    """
    data, _ = _build(catalog={"avg_rating": None, "rmp_rating": None,
                              "trace_rating": None, "total_reviews": 0})
    assert data["avgRating"] is None


def test_full_includes_reviews_trace_comments_and_reddit():
    data, _ = _build()
    assert "reviews" in data and "traceComments" in data and "redditMentions" in data
    assert data["reviews"][0]["course"] == "CS3500"
    # Unauthed: TRACE comment text is gated to "".
    assert data["traceComments"][0]["comment"] == ""
    assert data["redditMentions"][0]["sentiment"] == "negative"


def test_full_builds_trace_courses_with_hours_and_overall():
    data, _ = _build()
    courses = data["traceCourses"]
    assert len(courses) == 1
    c = courses[0]
    assert c["displayName"] == "CS3500: OOD"
    # hours weighted mean: (1*1+3.5*2+6*4+9*2+12*1)/(1+2+4+2+1)=62/10=6.2
    assert c["hoursPerWeek"] == 6.2


def test_full_rating_distribution_bucketed_by_course_code():
    data, _ = _build()
    dist = data["traceRatingCounts"]
    assert "CS3500" in dist
    assert dist["CS3500"]["count5"] == 7
    assert dist["CS3500"]["completed"] == 10


def test_full_blends_difficulty_from_rmp_and_trace():
    # rmp difficulty 3.5; trace challenge weighted mean:
    # (1*0+2*1+3*4+4*3+5*2)/(0+1+4+3+2)=36/10=3.6 → blended (3.5+3.6)/2=3.55→3.55
    data, _ = _build()
    assert data["difficulty"] == 3.55


def test_full_ratings_use_overall_course_not_law_overall_effectiveness():
    # Law sections carry TWO overall questions: 'Overall Course' and 'Overall
    # Effectiveness'. Ratings must count only 'Overall Course' — but the exclusion
    # must be exact, because the Bluera-era label ("What is your overall rating of
    # this instructor teaching effectiveness?") also contains "effectiveness" and
    # must keep counting.
    class LawQuery(RecordingQuery):
        def _rows_for(self, sql):
            s = sql.lower()
            if "from trace_scores" in s:
                base = {"course_id": 2, "term_id": 159, "display_name": "LAW6101: Con Law"}
                return [
                    {**base, "question": "Overall Course", "mean": 2.5,
                     "count_1": 1, "count_2": 0, "count_3": 0, "count_4": 1,
                     "count_5": 0, "completed": 2},
                    {**base, "question": "Overall Effectiveness", "mean": 3.0,
                     "count_1": 0, "count_2": 1, "count_3": 0, "count_4": 1,
                     "count_5": 0, "completed": 2},
                    {**base, "question": "What is your overall rating of this "
                     "instructor teaching effectiveness?", "mean": 4.5,
                     "count_1": 0, "count_2": 0, "count_3": 0, "count_4": 1,
                     "count_5": 1, "completed": 2},
                ]
            if "from trace_courses" in s:
                return [{"course_id": 2, "term_id": 159, "term_title": "Fall 2022 Law",
                         "department_name": "Law", "display_name": "LAW6101: Con Law",
                         "section": "1", "enrollment": 20, "instructor_id": 8}]
            return super()._rows_for(sql)

    rq = LawQuery()
    data = build_full("olin-guha", rq.query, rq.query_one, sanitize=lambda t: t,
                      fetch_reddit_mentions=_fake_fetch_reddit_mentions, is_authed=False)
    dist = data["traceRatingCounts"]["LAW6101"]
    assert dist["count4"] == 2, "Overall Course + Bluera overall only"
    assert dist["count2"] == 0, "Overall Effectiveness counts must not leak into ratings"
    assert dist["completed"] == 4


def test_full_404_when_professor_missing():
    rq = RecordingQuery()
    rq._rows_for = lambda sql: []  # nothing found
    result = build_full("nobody", rq.query, rq.query_one, sanitize=lambda t: t,
                        is_authed=False)
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


def test_old_trace_spelling_slug_resolves_to_the_merged_row():
    """"daniel-koloski" was the absorbed TRACE-only duplicate's slug; links to it
    have to land on the merged "dan-koloski" row instead of 404ing."""
    from professor_full import _resolve_professor
    merged = {"slug": "dan-koloski", "name_key": "dan koloski",
              "trace_name_key": "daniel koloski"}
    prof = _resolve_professor("daniel-koloski", lambda sql, params: None,
                              lambda keys: [merged] if keys == ["daniel koloski"] else [])
    assert prof is merged


def test_resolver_without_trace_lookup_is_unchanged():
    from professor_full import _resolve_professor
    assert _resolve_professor("nobody", lambda sql, params: None) is None


# ── v2: RMP in its own section ──────────────────────────────────────────────
# The same fake DB as above: RMP difficulty 3.5 on the catalog row, TRACE
# challenge 3.6 in trace_scores, and a blended avg_rating 4.2 / total_reviews 31.

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
                         fetch_reddit_mentions=_fake_fetch_reddit_mentions,
                         is_authed=False)
    return data, rq


def test_v2_splits_identity_from_rmp():
    data, _ = _build_v2()
    assert data["version"] == 2
    assert data["identity"] == {"slug": "olin-guha", "name": "Olin Guha",
                                "department": "Khoury", "college": None,
                                "imageUrl": None, "focusX": 50.0, "focusY": 30.0}
    assert data["sources"]["rmp"]["rating"] == 4.1
    assert data["sources"]["rmp"]["reviews"][0]["course"] == "CS3500"


def test_v2_serves_no_blended_field():
    """Every v1 field that pools RMP with TRACE is gone, not relabelled."""
    data, _ = _build_v2()
    for blended in ("avgRating", "totalRatings", "totalComments", "difficulty"):
        assert blended not in data
    # ...and RMP fields live only under sources.rmp.
    for rmp_field in ("rmpRating", "wouldTakeAgainPct", "professorUrl", "reviews"):
        assert rmp_field not in data


def test_v2_rmp_difficulty_is_not_averaged_with_trace():
    """v1 serves (3.5 + 3.6) / 2 = 3.55. v2 serves RMP's 3.5 under sources.rmp
    and TRACE's 3.6 as traceDifficulty, each on its own."""
    data, _ = _build_v2()
    assert data["sources"]["rmp"]["difficulty"] == 3.5
    assert data["traceDifficulty"] == 3.6


def test_v2_keeps_the_other_sources_untouched():
    v1, _ = _build()
    v2, _ = _build_v2()
    for field in ("traceCourses", "traceRatingCounts", "traceComments",
                  "redditMentions", "traceRating", "hoursPerWeek"):
        assert v2[field] == v1[field], field


def test_v2_costs_one_round_trip_more_than_v1():
    """The RMP link/summary lookup. Reviews are v1's, not fetched twice."""
    _, rq = _build_v2()
    assert len(rq.calls) <= 7, rq.calls
    assert rq.count_hitting("from rmp_reviews") == 1


def test_v2_without_an_rmp_record_says_so():
    data, _ = _build_v2(fetch_rmp_links=lambda slug: [])
    rmp_section = data["sources"]["rmp"]
    assert rmp_section["available"] is False
    assert rmp_section["reason"] == "no_rmp_record"
    assert rmp_section["rating"] is None
    # TRACE is still served beside it, labelled as TRACE.
    assert data["traceRating"] == 4.3


def test_v2_before_the_rmp_tables_exist_uses_the_catalogs_rmp_columns():
    data, _ = _build_v2(fetch_rmp_links=lambda slug: None, catalog={"num_ratings": 12})
    assert data["sources"]["rmp"]["rating"] == 4.1        # rmp_rating, not avg_rating 4.2
    assert data["sources"]["rmp"]["numRatings"] == 12     # num_ratings, not total_reviews 31
    assert data["sources"]["rmp"]["difficulty"] == 3.5    # RMP's, not the 3.55 blend


def test_v2_404_when_professor_missing():
    rq = RecordingQuery()
    rq._rows_for = lambda sql: []
    assert build_full_v2("nobody", rq.query, rq.query_one, sanitize=lambda t: t,
                         fetch_rmp_links=lambda slug: []) is None


def test_v1_is_unchanged_by_v2():
    """v1 is still the default response; it keeps its blend until Milestone 3."""
    data, _ = _build()
    assert data["difficulty"] == 3.55
    assert data["avgRating"] == 4.2
