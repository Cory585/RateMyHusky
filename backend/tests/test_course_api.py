"""Course page, professor profile/reviews routes and /api/dept-avg, all on
RMP data only (rmp_reviews.course_code, course_catalog, professors_catalog).

The DB is stubbed by patching server.query / query_one, keyed on the SQL text.
"""

import os

import pytest


@pytest.fixture
def server_mod(monkeypatch):
    os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
    os.environ.setdefault("JWT_SECRET", "test-secret")
    import server

    monkeypatch.setattr(server, "_get_pool", lambda: (_ for _ in ()).throw(AssertionError("no DB in test")), raising=False)
    monkeypatch.setattr(server, "cache_get", lambda key: None, raising=False)
    monkeypatch.setattr(server, "cache_set", lambda key, data: None, raising=False)
    return server


def _client(server, monkeypatch, one=None, many=None):
    seen = []

    def fake_query_one(sql, params=()):
        seen.append(sql)
        return (one or (lambda s, p: None))(sql, params)

    def fake_query(sql, params=()):
        seen.append(sql)
        return (many or (lambda s, p: []))(sql, params)

    monkeypatch.setattr(server, "query_one", fake_query_one)
    monkeypatch.setattr(server, "query", fake_query)
    c = server.app.test_client()
    c.seen = seen
    return c


COURSE = {"code": "CS3500", "name": "Object-Oriented Design", "department": "Khoury",
          "avg_rating": 4.256, "num_ratings": 7}

INSTRUCTOR_ROWS = [
    {"name": "Ada Byron", "slug": "ada-byron", "image_url": None,
     "would_take_again_pct": 81.234, "total_reviews": 40, "total_comments": 30,
     "num_reviews": 2, "avg_rating": 3.0, "avg_difficulty": 4.0, "latest_date": "2023-01-01"},
    {"name": "Bo Chen", "slug": "bo-chen", "image_url": "u.png",
     "would_take_again_pct": None, "total_reviews": 10, "total_comments": 5,
     "num_reviews": 5, "avg_rating": 4.666, "avg_difficulty": None, "latest_date": "2024-05-05"},
]


def _course_client(server, monkeypatch, course=COURSE):
    def one(sql, params):
        if "FROM course_catalog" in sql:
            return course
        if "MAX(date)" in sql:
            return {"latest_date": "2024-05-05"}
        raise AssertionError(sql)

    def many(sql, params):
        if "FROM rmp_reviews rr" in sql:
            return INSTRUCTOR_ROWS
        raise AssertionError(sql)

    return _client(server, monkeypatch, one, many)


def test_course_page_summary_shape(server_mod, monkeypatch):
    body = _course_client(server_mod, monkeypatch).get("/api/courses/cs 3500").get_json()
    assert set(body) == {"summary", "instructors"}
    assert body["summary"] == {
        "code": "CS3500", "name": "Object-Oriented Design", "department": "Khoury",
        "avgRating": 4.26, "numRatings": 7, "latestDate": "2024-05-05",
    }


def test_course_page_instructors_from_rmp_reviews_best_rated_first(server_mod, monkeypatch):
    body = _course_client(server_mod, monkeypatch).get("/api/courses/CS3500").get_json()
    names = [i["name"] for i in body["instructors"]]
    assert names == ["Bo Chen", "Ada Byron"]
    ada = body["instructors"][1]
    assert ada == {
        "name": "Ada Byron", "slug": "ada-byron", "imageUrl": None,
        "avgRating": 3.0, "numReviews": 2, "courseAvgDifficulty": 4.0,
        "wouldTakeAgainPct": 81.2, "totalReviews": 40, "totalComments": 30,
        "latestDate": "2023-01-01",
    }
    assert body["instructors"][0]["courseAvgDifficulty"] is None


def test_course_page_serves_unnamed_course_with_null_name(server_mod, monkeypatch):
    course = {**COURSE, "name": None}
    body = _course_client(server_mod, monkeypatch, course).get("/api/courses/CS3500").get_json()
    assert body["summary"]["name"] is None


def test_course_page_has_no_removed_fields_or_queries(server_mod, monkeypatch):
    c = _course_client(server_mod, monkeypatch)
    resp = c.get("/api/courses/CS3500")
    body = resp.get_json()
    for gone in ("sections", "questionScores"):
        assert gone not in body
    for gone in ("isTopics", "avgEnrollment", "latestTermTitle", "ratingCount"):
        assert gone not in body["summary"]
    assert "courseAvgHoursPerWeek" not in body["instructors"][0]
    assert not any("trace" in sql.lower() for sql in c.seen)
    assert resp.headers["Cache-Control"] == "public, max-age=3600"


def test_course_page_404_for_unknown_course(server_mod, monkeypatch):
    c = _client(server_mod, monkeypatch)
    assert c.get("/api/courses/NOPE9999").status_code == 404


# ── professor profile / reviews ──

PROF = {"name": "Ada Byron", "slug": "ada-byron", "name_key": "ada byron",
        "department": "Khoury", "rmp_rating": 4.0, "avg_rating": 4.0,
        "difficulty": 3.2, "would_take_again_pct": 90.0, "total_reviews": 12,
        "professor_url": None, "image_url": None}


def _prof_client(server, monkeypatch):
    def one(sql, params):
        if "FROM professors_catalog" in sql:
            return PROF
        raise AssertionError(sql)

    def many(sql, params):
        if "GROUP BY rr.course_code" in sql:
            return [{"code": "CS3500", "name": None, "num_reviews": 3, "avg_rating": 4.0,
                     "avg_difficulty": 3.0, "latest_date": "2024-01-01"}]
        if "FROM rmp_reviews" in sql:
            return [{"course": "CS3500", "quality": 4, "difficulty": 3, "date": "2024-01-01",
                     "tags": "", "attendance": "", "grade": "", "textbook": "",
                     "online_class": "", "comment": "Solid."}]
        return []

    return _client(server, monkeypatch, one, many)


def test_profile_serves_courses_and_no_trace_fields(server_mod, monkeypatch):
    c = _prof_client(server_mod, monkeypatch)
    body = c.get("/api/professors/ada-byron").get_json()
    assert body["difficulty"] == 3.2
    assert body["courses"] == [{"code": "CS3500", "name": None, "numReviews": 3,
                                "avgRating": 4.0, "avgDifficulty": 3.0,
                                "latestDate": "2024-01-01"}]
    for gone in ("traceRating", "traceCourses", "traceRatingCounts", "hoursPerWeek"):
        assert gone not in body
    assert not any("trace" in sql.lower() for sql in c.seen)


def test_reviews_route_returns_reviews_and_reddit_only(server_mod, monkeypatch):
    monkeypatch.setattr(server_mod, "fetch_reddit_mentions", lambda slug, q: [])
    body = _prof_client(server_mod, monkeypatch).get("/api/professors/ada-byron/reviews").get_json()
    assert set(body) == {"reviews", "redditMentions"}
    assert body["reviews"][0]["comment"] == "Solid."


def test_full_route_has_courses_reviews_and_no_trace_comments(server_mod, monkeypatch):
    monkeypatch.setattr(server_mod, "fetch_reddit_mentions", lambda slug, q: [])
    monkeypatch.setattr(server_mod, "_department_colleagues", lambda dept, slug: [])
    body = _prof_client(server_mod, monkeypatch).get("/api/professors/ada-byron/full").get_json()
    assert body["courses"][0]["code"] == "CS3500"
    assert body["reviews"] and "traceComments" not in body


def test_profile_404(server_mod, monkeypatch):
    c = _client(server_mod, monkeypatch)
    assert c.get("/api/professors/nobody").status_code == 404


# ── /api/dept-avg ──

def test_dept_avg_averages_catalog_rows(server_mod, monkeypatch):
    def one(sql, params):
        assert "FROM professors_catalog" in sql and params == ("Khoury",)
        return {"avg_rating": 4.123, "difficulty": 3.456, "would_take_again_pct": 77.77,
                "num_professors": 42}

    body = _client(server_mod, monkeypatch, one).get("/api/dept-avg?department=Khoury").get_json()
    assert body == {"avgRating": 4.12, "difficulty": 3.46,
                    "wouldTakeAgainPct": 77.8, "numProfessors": 42}


def test_dept_avg_null_fields_when_no_data(server_mod, monkeypatch):
    def one(sql, params):
        return {"avg_rating": None, "difficulty": None, "would_take_again_pct": None,
                "num_professors": 0}

    body = _client(server_mod, monkeypatch, one).get("/api/dept-avg?department=Nowhere").get_json()
    assert body == {"avgRating": None, "difficulty": None,
                    "wouldTakeAgainPct": None, "numProfessors": 0}


def test_dept_avg_without_department_is_empty(server_mod, monkeypatch):
    body = _client(server_mod, monkeypatch).get("/api/dept-avg").get_json()
    assert body["numProfessors"] == 0 and body["avgRating"] is None


def test_old_trace_dept_avg_route_is_gone(server_mod, monkeypatch):
    assert _client(server_mod, monkeypatch).get("/api/trace-dept-avg?department=x").status_code == 404
