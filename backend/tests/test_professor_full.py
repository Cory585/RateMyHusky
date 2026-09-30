"""The professor page payload (/api/professors/<slug>/full, spec §4.2).

A fake query records every statement, so the round-trip budget and the tables
touched are pinned alongside the contract's exact key sets.
"""

from professor_full import build_payload, normalize_course_code

CATALOG = {"slug": "olin-guha", "name": "Olin Guha", "name_key": "olin guha",
           "department": "Khoury", "college": "Khoury", "avg_rating": 4.2,
           "rmp_rating": 4.2, "num_ratings": 57, "total_reviews": 57,
           "would_take_again_pct": 88.0, "difficulty": 3.1,
           "professor_url": "https://www.ratemyprofessors.com/professor/1",
           "image_url": None, "focus_x": None, "focus_y": None, "total_comments": 3}

REVIEWS = [
    {"course": "CS3500", "quality": 5, "difficulty": 3, "date": "2024", "tags": "",
     "attendance": "", "grade": "A", "textbook": "", "online_class": "", "comment": "Great."},
    {"course": "cs 3500", "quality": 3, "difficulty": 4, "date": "2023", "tags": "",
     "attendance": "", "grade": "B", "textbook": "", "online_class": "", "comment": ""},
    {"course": "CS2500 ", "quality": 4, "difficulty": 2, "date": "2022", "tags": "",
     "attendance": "", "grade": "", "textbook": "", "online_class": "", "comment": "Fine."},
    {"course": "FUNDIES", "quality": 2, "difficulty": 5, "date": "2021", "tags": "",
     "attendance": "", "grade": "", "textbook": "", "online_class": "", "comment": "Hard."},
]

MENTIONS = [{"body": "guha is great", "sentiment": "positive", "sentiment_score": 0.6,
             "score": 12, "subreddit": "NEU", "permalink": "/r/x", "created_utc": None}]


class FakeDB:
    def __init__(self, catalog=CATALOG, reviews=REVIEWS, names=None):
        self.catalog, self.reviews = catalog, reviews
        self.names = names if names is not None else {"CS3500": "Object-Oriented Design"}
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        s = " ".join(sql.split()).lower()
        if "from rmp_reviews" in s:
            return [dict(r) for r in self.reviews]
        if "from course_catalog" in s:
            return [{"code": c, "name": n} for c, n in self.names.items() if c in params[0]]
        raise AssertionError(f"unexpected query: {sql}")

    def query_one(self, sql, params=None):
        self.calls.append((sql, params))
        s = " ".join(sql.split()).lower()
        if "from professors_catalog where slug" in s:
            return dict(self.catalog) if self.catalog and params[0] == self.catalog["slug"] else None
        if "from professors_catalog where name_key" in s:
            return dict(self.catalog) if self.catalog and params[0] == self.catalog["name_key"] else None
        raise AssertionError(f"unexpected query_one: {sql}")


def _build(slug="olin-guha", db=None, mentions=MENTIONS):
    db = db or FakeDB()
    seen = {}

    def fetch_reddit_mentions(s, query_fn, mod_filter=""):
        # The Reddit round trip itself is counted by the route test.
        seen["slug"] = s
        return [dict(m) for m in mentions]

    data = build_payload(slug, db.query, db.query_one, lambda t: t, fetch_reddit_mentions)
    return data, db, seen


# ── contract key sets (spec §4.2) ──

def test_top_level_keys():
    data, _, _ = _build()
    assert set(data) == {"version", "identity", "summary", "sources", "courses", "redditMentions"}
    assert data["version"] == 2


def test_identity_keys():
    data, _, _ = _build()
    assert data["identity"] == {"slug": "olin-guha", "name": "Olin Guha", "department": "Khoury",
                                "college": "Khoury", "imageUrl": None, "focusX": 50.0, "focusY": 30.0}


def test_summary_keys_and_values():
    data, _, _ = _build()
    assert data["summary"] == {
        "rating": 4.2, "difficulty": 3.1, "wouldTakeAgainPct": 88.0, "numRatings": 57,
        "numComments": 4,   # 3 RMP reviews with text + 1 Reddit mention
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
    assert all(set(c) == {"code", "name", "rating", "difficulty", "numRatings", "ratingDistribution"}
               for c in data["courses"])


# ── courses[] ──

def test_courses_group_messy_codes():
    data, _, _ = _build()
    by_code = {c["code"]: c for c in data["courses"]}
    assert set(by_code) == {"CS3500", "CS2500"}          # "FUNDIES" dropped
    assert by_code["CS3500"]["numRatings"] == 2           # "CS3500" + "cs 3500"
    assert by_code["CS3500"]["rating"] == 4.0
    assert by_code["CS3500"]["difficulty"] == 3.5
    assert by_code["CS3500"]["ratingDistribution"] == {"1": 0, "2": 0, "3": 1, "4": 0, "5": 1}
    assert by_code["CS3500"]["name"] == "Object-Oriented Design"
    assert by_code["CS2500"]["name"] is None               # not in course_catalog


def test_courses_ordered_by_rating_count_then_code():
    data, _, _ = _build()
    assert [c["code"] for c in data["courses"]] == ["CS3500", "CS2500"]


def test_normalize_course_code():
    assert normalize_course_code("cs 2500") == "CS2500"
    assert normalize_course_code("CS2500 ") == "CS2500"
    assert normalize_course_code("FUNDIES") is None
    assert normalize_course_code("") is None
    assert normalize_course_code(None) is None
    assert normalize_course_code("CS25000") is None


# ── edge cases ──

def test_unrated_professor_is_all_null():
    unrated = {**CATALOG, "avg_rating": None, "rmp_rating": None, "num_ratings": 0,
               "total_reviews": 0, "would_take_again_pct": None, "difficulty": None,
               "professor_url": None}
    data, _, _ = _build(db=FakeDB(catalog=unrated, reviews=[]), mentions=[])
    s = data["summary"]
    assert (s["rating"], s["difficulty"], s["wouldTakeAgainPct"], s["numRatings"]) == (None, None, None, 0)
    assert s["bySource"] == {"rmp": {"rating": None, "numRatings": 0}}
    rmp = data["sources"]["rmp"]
    assert rmp["available"] is False
    assert (rmp["rating"], rmp["difficulty"], rmp["wouldTakeAgainPct"]) == (None, None, None)
    assert data["courses"] == []


def test_zero_ratings_are_null_not_zero():
    zeros = {**CATALOG, "avg_rating": 0.0, "rmp_rating": 0.0, "difficulty": 0.0}
    data, _, _ = _build(db=FakeDB(catalog=zeros))
    assert data["summary"]["rating"] is None
    assert data["summary"]["difficulty"] is None


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

def test_at_most_four_statements_and_only_live_tables():
    _, db, _ = _build()
    assert len(db.calls) <= 4
    tables = " ".join(sql.lower() for sql, _ in db.calls)
    for table in ("professors_catalog", "rmp_reviews", "course_catalog"):
        assert table in tables


def test_no_course_name_lookup_without_codes():
    _, db, _ = _build(db=FakeDB(reviews=[{**REVIEWS[3]}]))
    assert not any("course_catalog" in sql.lower() for sql, _ in db.calls)
