"""The professor page payload (/api/professors/<slug>/full, spec §4.2).

A fake query records every statement, so the round-trip budget and the tables
touched are pinned alongside the contract's exact key sets.
"""

from professor_full import build_payload

CATALOG = {"slug": "olin-guha", "name": "Olin Guha", "name_key": "olin guha",
           "department": "Khoury", "college": "Khoury", "avg_rating": 4.2,
           "rmp_rating": 4.2, "num_ratings": 57, "total_reviews": 57,
           "would_take_again_pct": 88.0, "difficulty": 3.1,
           "professor_url": "https://www.ratemyprofessors.com/professor/1",
           "image_url": None, "focus_x": None, "focus_y": None, "total_comments": 3}

REVIEWS = [
    {"course": "CS3500", "course_code": "CS3500", "quality": 5, "difficulty": 3, "date": "2024", "tags": "",
     "attendance": "", "grade": "A", "textbook": "", "online_class": "", "comment": "Great."},
    {"course": "cs 3500", "course_code": "CS3500", "quality": 3, "difficulty": 4, "date": "2023", "tags": "",
     "attendance": "", "grade": "B", "textbook": "", "online_class": "", "comment": ""},
    {"course": "CS2500 ", "course_code": "CS2500", "quality": 4, "difficulty": 2, "date": "2022", "tags": "",
     "attendance": "", "grade": "", "textbook": "", "online_class": "", "comment": "Fine."},
    {"course": "FUNDIES", "course_code": None, "quality": 2, "difficulty": 5, "date": "2021", "tags": "",
     "attendance": "", "grade": "", "textbook": "", "online_class": "", "comment": "Hard."},
]

MENTIONS = [{"body": "guha is great", "sentiment": "positive", "sentiment_score": 0.6,
             "score": 12, "subreddit": "NEU", "permalink": "/r/x", "created_utc": None}]


SUMMARY_ROWS = [
    {"course_code": "", "source": "rmp", "rating": 4.2, "difficulty": 3.1, "would_take_again_pct": 88.0,
     "num_ratings": 57, "num_comments": 3, "hours_per_week": None,
     "rating_distribution": {"1": 1, "2": 2, "3": 4, "4": 20, "5": 30}, "grade_distribution": {"A": 9},
     "course_name": None},
    {"course_code": "", "source": "blend", "rating": 4.2, "difficulty": 3.1, "would_take_again_pct": 88.0,
     "num_ratings": 57, "num_comments": 3, "hours_per_week": None,
     "rating_distribution": {"1": 1, "2": 2, "3": 4, "4": 20, "5": 30}, "grade_distribution": {"A": 9},
     "course_name": None},
    {"course_code": "CS3500", "source": "blend", "rating": 4.0, "difficulty": 3.5, "would_take_again_pct": None,
     "num_ratings": 2, "num_comments": 1, "hours_per_week": None,
     "rating_distribution": {"3": 1, "5": 1}, "grade_distribution": {}, "course_name": "Object-Oriented Design"},
    {"course_code": "CS2500", "source": "blend", "rating": 4.0, "difficulty": 2.0, "would_take_again_pct": None,
     "num_ratings": 1, "num_comments": 1, "hours_per_week": None,
     "rating_distribution": {"4": 1}, "grade_distribution": {}, "course_name": None},
    {"course_code": "CS3500", "source": "rmp", "rating": 4.0, "difficulty": 3.5, "would_take_again_pct": None,
     "num_ratings": 2, "num_comments": 1, "hours_per_week": None,
     "rating_distribution": {"3": 1, "5": 1}, "grade_distribution": {}, "course_name": "Object-Oriented Design"},
]


class FakeDB:
    def __init__(self, catalog=CATALOG, reviews=REVIEWS, summaries=SUMMARY_ROWS):
        self.catalog, self.reviews, self.summaries = catalog, reviews, summaries
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        s = " ".join(sql.split()).lower()
        if "from rmp_reviews" in s:
            return [dict(r) for r in self.reviews]
        if "from source_summary" in s:
            return [dict(r) for r in self.summaries]
        if "from banner_course_instructors" in s:
            return []
        raise AssertionError(f"unexpected query: {sql}")

    def query_one(self, sql, params=None):
        self.calls.append((sql, params))
        s = " ".join(sql.split()).lower()
        if "from professors_catalog where slug" in s:
            return dict(self.catalog) if self.catalog and params[0] == self.catalog["slug"] else None
        if "from professors_catalog where name_key" in s:
            return dict(self.catalog) if self.catalog and params[0] == self.catalog["name_key"] else None
        raise AssertionError(f"unexpected query_one: {sql}")


AWARDS = {"olin guha": [{"award": "University Excellence in Teaching Award", "awardingBody": "Northeastern University",
                          "year": 2023, "yearLabel": "2022\u201323", "sourceUrl": "https://example.edu/awards"}]}


def _build(slug="olin-guha", db=None, mentions=MENTIONS, awards=AWARDS, history=None):
    """`history`: a fake course_history_for. None runs the real one against FakeDB,
    which has no Banner rows."""
    db = db or FakeDB()
    seen = {}

    def fetch_reddit_mentions(s, query_fn, mod_filter=""):
        # The Reddit round trip itself is counted by the route test.
        seen["slug"] = s
        return [dict(m) for m in mentions]

    extra = {} if history is None else {"course_history_for": history}
    data = build_payload(slug, db.query, db.query_one, lambda t: t, fetch_reddit_mentions,
                         awards_for=lambda key: [dict(a) for a in awards.get(key, [])], **extra)
    return data, db, seen


# ── contract key sets (spec §4.2) ──

def test_top_level_keys():
    data, _, _ = _build()
    assert set(data) == {"version", "identity", "summary", "sources", "courses", "redditMentions",
                         "awards", "lastTaught"}
    assert data["version"] == 2


def test_awards_join_on_the_catalog_name_key():
    data, _, _ = _build()
    assert data["awards"] == AWARDS["olin guha"]


def test_no_awards_is_an_empty_list():
    data, _, _ = _build(awards={})
    assert data["awards"] == []


def test_identity_keys():
    data, _, _ = _build()
    assert data["identity"] == {"slug": "olin-guha", "name": "Olin Guha", "department": "Khoury",
                                "college": "Khoury", "imageUrl": None, "focusX": 50.0, "focusY": 30.0}


def test_summary_keys_and_values():
    data, _, _ = _build()
    assert data["summary"] == {
        "rating": 4.2, "difficulty": 3.1, "wouldTakeAgainPct": 88.0, "numRatings": 57,
        "numComments": 4,   # 3 RMP comments + 1 Reddit mention
        "hoursPerWeek": None,
        "bySource": {"rmp": {"rating": 4.2, "numRatings": 57}},
    }


def test_sources_holds_rmp_only_with_exact_keys():
    data, _, _ = _build()
    assert set(data["sources"]) == {"rmp"}
    assert set(data["sources"]["rmp"]) == {
        "available", "rating", "difficulty", "wouldTakeAgainPct", "numRatings",
        "professorUrl", "ratingDistribution", "gradeDistribution", "reviews"}


def test_course_row_keys():
    data, _, _ = _build()
    assert all(set(c) == {"code", "name", "rating", "difficulty", "numRatings", "ratingDistribution",
                          "terms", "lastTerm", "hasPage"} for c in data["courses"])


# ── courses[] ──

def test_courses_come_from_the_course_rows():
    data, _, _ = _build()
    by_code = {c["code"]: c for c in data["courses"]}
    assert [c["code"] for c in data["courses"]] == ["CS3500", "CS2500"]
    assert by_code["CS3500"]["numRatings"] == 2
    assert by_code["CS3500"]["rating"] == 4.0
    assert by_code["CS3500"]["name"] == "Object-Oriented Design"
    assert by_code["CS3500"]["ratingDistribution"] == {"1": 0, "2": 0, "3": 1, "4": 0, "5": 1}
    assert by_code["CS2500"]["name"] is None


def test_courses_ordered_by_rating_count_then_code():
    data, _, _ = _build()
    assert [c["code"] for c in data["courses"]] == ["CS3500", "CS2500"]


def test_reviews_carry_their_pipeline_course_code():
    data, _, _ = _build()
    assert [r["courseCode"] for r in data["sources"]["rmp"]["reviews"]] == ["CS3500", "CS3500", "CS2500", None]


def test_rmp_distributions_come_from_the_summary_row():
    data, _, _ = _build()
    assert data["sources"]["rmp"]["ratingDistribution"] == {"1": 1, "2": 2, "3": 4, "4": 20, "5": 30}
    assert data["sources"]["rmp"]["gradeDistribution"] == {"A": 9}


# ── Banner teaching history ──

HISTORY = [
    {"code": "CS3100", "name": "Program Design and Implementation 2", "termsTaught": 3,
     "lastTerm": "Fall 2026", "recentTerms": ["Fall 2026", "Spring 2026", "Fall 2025"],
     "avgClassSize": 111, "hasPage": False},
    {"code": "CS2500", "name": "Fundamentals of Computer Science 1", "termsTaught": 2,
     "lastTerm": "Fall 2024", "recentTerms": ["Fall 2024", "Fall 2023"],
     "avgClassSize": 80, "hasPage": False},
]


def _with_history(history=HISTORY, **kw):
    calls = []

    def course_history_for(slug, query):
        calls.append(slug)
        return [dict(h) for h in history]

    data, db, seen = _build(history=course_history_for, **kw)
    return data, calls


def test_taught_courses_come_first_in_history_order_then_most_rated():
    data, _ = _with_history()
    assert [c["code"] for c in data["courses"]] == ["CS3100", "CS2500", "CS3500"]


def test_banner_only_course_has_no_ratings():
    data, _ = _with_history()
    assert data["courses"][0] == {
        "code": "CS3100", "name": "Program Design and Implementation 2", "rating": None,
        "difficulty": None, "numRatings": 0, "ratingDistribution": None,
        "terms": ["Fall 2026", "Spring 2026", "Fall 2025"], "lastTerm": "Fall 2026", "hasPage": False}


def test_reviewed_course_with_history_keeps_its_ratings_and_gains_terms():
    data, _ = _with_history()
    cs2500 = data["courses"][1]
    assert (cs2500["rating"], cs2500["numRatings"], cs2500["name"]) == (4.0, 1, None)
    assert cs2500["terms"] == ["Fall 2024", "Fall 2023"]
    assert cs2500["lastTerm"] == "Fall 2024"


def test_two_history_rows_for_one_code_make_one_course_row_newest_wins():
    older = {**HISTORY[1], "code": "CS2500", "lastTerm": "Fall 2020", "recentTerms": ["Fall 2020"]}
    data, _ = _with_history(history=[HISTORY[1], older])
    rows = [c for c in data["courses"] if c["code"] == "CS2500"]
    assert len(rows) == 1
    assert (rows[0]["lastTerm"], rows[0]["terms"]) == ("Fall 2024", ["Fall 2024", "Fall 2023"])
    assert rows[0]["rating"] == 4.0 and rows[0]["numRatings"] == 1


def test_reviewed_course_without_history_has_no_terms():
    data, _ = _with_history()
    cs3500 = data["courses"][2]
    assert (cs3500["terms"], cs3500["lastTerm"], cs3500["hasPage"]) == ([], None, True)


def test_reviewed_course_missing_from_the_catalog_has_no_page():
    data, _, _ = _build()
    by_code = {c["code"]: c for c in data["courses"]}
    assert by_code["CS2500"]["hasPage"] is False   # course_name is None: no course_catalog row
    assert by_code["CS3500"]["hasPage"] is True


def test_last_taught_is_the_newest_history_row():
    data, _ = _with_history()
    assert data["lastTaught"] == "Fall 2026"


def test_no_history_means_no_last_taught():
    data, _ = _with_history(history=[])
    assert data["lastTaught"] is None
    assert [c["code"] for c in data["courses"]] == ["CS3500", "CS2500"]


def test_history_is_read_for_the_canonical_slug():
    _, calls = _with_history(slug="olin-guha", db=FakeDB(catalog={**CATALOG, "slug": "olin-guha-2"}))
    assert calls == ["olin-guha-2"]


def test_history_failure_leaves_courses_as_today(caplog):
    def broken(slug, query):
        raise RuntimeError("relation banner_course_instructors does not exist")

    data, _, _ = _build(history=broken)
    assert [c["code"] for c in data["courses"]] == ["CS3500", "CS2500"]
    assert all(c["terms"] == [] and c["lastTerm"] is None for c in data["courses"])
    assert data["lastTaught"] is None
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1


def test_history_failure_flags_the_payload_so_it_is_not_cached():
    def broken(slug, query):
        raise RuntimeError("banner down")

    data, _, _ = _build(history=broken)
    assert data["_historyFailed"] is True


def test_history_success_has_no_failure_flag():
    data, _ = _with_history()
    assert "_historyFailed" not in data


# ── edge cases ──

def test_unrated_professor_is_all_null():
    empty = {"rating": None, "difficulty": None, "would_take_again_pct": None, "num_ratings": 0,
             "num_comments": 0, "rating_distribution": {}, "grade_distribution": {}}
    unrated = {**CATALOG, "avg_rating": None, "rmp_rating": None, "num_ratings": 0,
               "total_reviews": 0, "would_take_again_pct": None, "difficulty": None,
               "professor_url": None}
    summaries = [{**SUMMARY_ROWS[0], **empty}, {**SUMMARY_ROWS[1], **empty}]
    data, _, _ = _build(db=FakeDB(catalog=unrated, reviews=[], summaries=summaries), mentions=[])
    s = data["summary"]
    assert (s["rating"], s["difficulty"], s["wouldTakeAgainPct"], s["numRatings"]) == (None, None, None, 0)
    assert s["bySource"] == {"rmp": {"rating": None, "numRatings": 0}}
    rmp = data["sources"]["rmp"]
    assert rmp["available"] is False
    assert (rmp["rating"], rmp["difficulty"], rmp["wouldTakeAgainPct"]) == (None, None, None)
    assert data["courses"] == []


def test_zero_ratings_are_null_not_zero():
    zeros = [{**r, "rating": 0.0, "difficulty": 0.0} for r in SUMMARY_ROWS]
    data, _, _ = _build(db=FakeDB(summaries=zeros))
    assert data["summary"]["rating"] is None
    assert data["summary"]["difficulty"] is None


def test_zero_would_take_again_is_a_real_percentage():
    zeros = [{**r, "would_take_again_pct": 0.0} for r in SUMMARY_ROWS]
    data, _, _ = _build(db=FakeDB(summaries=zeros))
    assert data["summary"]["wouldTakeAgainPct"] == 0.0
    assert data["sources"]["rmp"]["wouldTakeAgainPct"] == 0.0


def test_alias_fallback_serves_the_canonical_slug():
    # No row has slug "olin-guha", so the lookup falls back to name_key "olin guha".
    db = FakeDB(catalog={**CATALOG, "slug": "olin-guha-2"})
    data, db, seen = _build(slug="olin-guha", db=db)
    assert data["identity"]["slug"] == "olin-guha-2"
    assert seen["slug"] == "olin-guha-2"


def test_missing_professor_is_none():
    data, _, _ = _build(slug="nobody", db=FakeDB())
    assert data is None


# ── round trips ──

def test_at_most_five_statements_and_only_live_tables():
    _, db, _ = _build()
    assert len(db.calls) <= 5
    tables = " ".join(sql.lower() for sql, _ in db.calls)
    for table in ("professors_catalog", "rmp_reviews", "source_summary", "banner_course_instructors"):
        assert table in tables
