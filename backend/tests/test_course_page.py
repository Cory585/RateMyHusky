"""The course page payload (/api/courses/<code>, spec §4.3)."""

import copy
import os

import pytest
import psycopg2.errors

os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
import server  # noqa: E402
from course_page import build_course  # noqa: E402

COURSE = {"code": "CS3500", "name": "Object-Oriented Design", "department": "Computer Science",
          "description": "Design.", "credit_hours": "4", "prerequisites": "CS2510", "corequisites": None,
          "nupath": ["NUpath Analyzing/Using Data"]}
_NONE = {"rating": None, "difficulty": None, "hours_per_week": None, "image_url": None,
         "focus_x": None, "focus_y": None, "name": None}
ROWS = [
    {**_NONE, "professor_slug": "", "source": "blend", "rating": 4.25, "difficulty": 3.25, "num_ratings": 40,
     "hours_per_week": 6.5},
    {**_NONE, "professor_slug": "", "source": "rmp", "rating": 4.25, "difficulty": 3.25, "num_ratings": 40},
    {**_NONE, "professor_slug": "olin-guha", "source": "blend", "rating": 4.5, "difficulty": 3.0,
     "num_ratings": 30, "name": "Olin Guha"},
    {**_NONE, "professor_slug": "amal-ahmed", "source": "blend", "rating": 3.5, "difficulty": 4.0,
     "num_ratings": 10, "name": "Amal Ahmed", "image_url": "https://img/a.jpg", "focus_x": 40.0,
     "focus_y": 20.0},
]


class FakeDB:
    def __init__(self, course=COURSE, rows=ROWS, newest="202710"):
        self.course, self.rows, self.newest, self.calls = course, rows, newest, []

    def query(self, sql, params=None):
        self.calls.append(sql)
        assert "FROM source_summary" in sql
        return [dict(r) for r in self.rows]

    def query_one(self, sql, params=None):
        self.calls.append(sql)
        if "FROM banner_terms" in sql:
            return {"code": self.newest}
        assert "FROM course_catalog" in sql
        return dict(self.course) if self.course and params[0] == self.course["code"] else None


def test_contract_keys():
    data = build_course("CS3500", FakeDB().query, FakeDB().query_one)
    assert set(data) == {"code", "name", "department", "catalog", "summary", "professors", "history"}
    assert set(data["catalog"]) == {"description", "credits", "prerequisites", "corequisites", "nupath"}
    assert set(data["summary"]) == {"rating", "difficulty", "numRatings", "hoursPerWeek", "bySource"}
    assert all(set(p) == {"slug", "name", "imageUrl", "focusX", "focusY", "rating", "difficulty",
                          "numRatings", "lastTerm", "avgClassSize"} for p in data["professors"])


def test_catalog_details_come_from_the_course_row():
    db = FakeDB()
    assert build_course("CS3500", db.query, db.query_one)["catalog"] == {
        "description": "Design.", "credits": "4", "prerequisites": "CS2510", "corequisites": None,
        "nupath": ["NUpath Analyzing/Using Data"]}


def test_summary_comes_from_the_course_blend_row():
    db = FakeDB()
    data = build_course("CS3500", db.query, db.query_one)
    assert data["summary"] == {"rating": 4.25, "difficulty": 3.25, "numRatings": 40,
                               "hoursPerWeek": 6.5,
                               "bySource": {"rmp": {"rating": 4.25, "numRatings": 40}}}


def test_professor_rows_default_the_photo_focus():
    db = FakeDB()
    p = build_course("CS3500", db.query, db.query_one)["professors"][0]
    assert p == {"slug": "olin-guha", "name": "Olin Guha", "imageUrl": None, "focusX": 50.0,
                 "focusY": 30.0, "rating": 4.5, "difficulty": 3.0, "numRatings": 30,
                 "lastTerm": None, "avgClassSize": None}


def test_professor_missing_from_the_catalog_is_left_out():
    rows = [*ROWS, {**_NONE, "professor_slug": "gone", "source": "blend", "rating": 5.0,
                    "difficulty": 1.0, "num_ratings": 99}]
    db = FakeDB(rows=rows)
    data = build_course("CS3500", db.query, db.query_one)
    assert [p["slug"] for p in data["professors"]] == ["olin-guha", "amal-ahmed"]


def test_course_without_reviews_is_200_with_nulls():
    db = FakeDB(rows=[])
    data = build_course("CS3500", db.query, db.query_one)
    assert data["summary"]["rating"] is None and data["summary"]["difficulty"] is None
    assert data["summary"]["numRatings"] == 0
    assert data["professors"] == []


def test_unknown_code_is_none():
    db = FakeDB()
    assert build_course("ZZZZ9999", db.query, db.query_one) is None


def test_two_statements_before_banner_history():
    db = FakeDB()
    build_course("CS3500", db.query, db.query_one, history_for=lambda c, q, qo: None)
    assert len(db.calls) == 2


# ── Banner teaching history ──

def _inst(slug, name, code, terms, size=None):
    return {"name": name, "slug": slug, "termsTaught": len(terms), "lastTerm": terms[0],
            "lastTermCode": code, "recentTerms": terms, "avgClassSize": size,
            "imageUrl": f"https://img/{slug}.jpg" if slug else None, "focusX": 50.0, "focusY": 30.0}


HISTORY = {
    "pattern": "Fall and Spring", "seasonYears": {"fall": 4, "spring": 4, "summer": 0},
    "windowYears": 4, "lastOffered": "Fall 2026", "offeredNow": ["Fall 2026"],
    "creditHours": "4", "typicalClassSize": 43, "instructorCount": 3,
    "replacedBy": None, "replaces": None,
    "instructors": [
        _inst("olin-guha", "Olin Guha", "202710",
              ["Fall 2026", "Summer 1 2026", "Spring 2026", "Fall 2025", "Spring 2025"], 40),
        _inst(None, "Jane Q Doe", "202630", ["Spring 2026"]),
        _inst("amal-ahmed", "Amal Ahmed", "202530", ["Spring 2025", "Fall 2024"], 35),
    ],
}


def _with_history(history=HISTORY, newest="202710"):
    db = FakeDB(newest=newest)
    return build_course("CS3500", db.query, db.query_one,
                        history_for=lambda c, q, qo: copy.deepcopy(history))


def test_history_is_course_history_plus_recent_instructors():
    h = _with_history()["history"]
    assert set(h) == set(HISTORY) | {"recentInstructors"}
    assert h["lastOffered"] == "Fall 2026"


def test_recent_is_a_year_back_from_the_newest_term():
    # newest 202710 (Fall 2026) -> cutoff 202610 (Fall 2025): Amal's Spring 2025 is out.
    recent = _with_history()["history"]["recentInstructors"]
    assert [r["name"] for r in recent] == ["Olin Guha", "Jane Q Doe"]


def test_recent_window_includes_the_cutoff_term():
    # newest 202630 -> cutoff 202530: Amal's last term is exactly the cutoff.
    recent = _with_history(newest="202630")["history"]["recentInstructors"]
    assert "Amal Ahmed" in [r["name"] for r in recent]


def test_recent_terms_are_cut_to_the_window():
    olin = _with_history()["history"]["recentInstructors"][0]
    assert olin["terms"] == ["Fall 2026", "Summer 1 2026", "Spring 2026", "Fall 2025"]


def test_recent_drops_terms_it_cannot_read():
    history = {**HISTORY, "instructors": [
        _inst("olin-guha", "Olin Guha", "202710", ["Fall 2026", "Fall A 2026", "Spring 2026"])]}
    olin = _with_history(history)["history"]["recentInstructors"][0]
    assert olin["terms"] == ["Fall 2026", "Spring 2026"]


def test_recent_card_fields_and_this_courses_rating():
    olin = _with_history()["history"]["recentInstructors"][0]
    assert olin == {"slug": "olin-guha", "name": "Olin Guha", "imageUrl": "https://img/olin-guha.jpg",
                    "focusX": 50.0, "focusY": 30.0, "rating": 4.5,
                    "terms": ["Fall 2026", "Summer 1 2026", "Spring 2026", "Fall 2025"],
                    "avgClassSize": 40}


def test_unmatched_instructor_has_no_slug_photo_or_rating():
    jane = _with_history()["history"]["recentInstructors"][1]
    assert (jane["slug"], jane["imageUrl"], jane["rating"]) == (None, None, None)


def test_matched_instructor_with_no_reviews_here_has_no_rating():
    history = {**HISTORY, "instructors": [_inst("new-prof", "New Prof", "202710", ["Fall 2026"])]}
    assert _with_history(history)["history"]["recentInstructors"][0]["rating"] is None


def test_dormant_course_has_no_recent_instructors_but_keeps_history():
    history = {**HISTORY, "instructors": [HISTORY["instructors"][2]]}
    h = _with_history(history)["history"]
    assert h["recentInstructors"] == []
    assert h["pattern"] == "Fall and Spring"


def test_recent_is_capped_at_ten_newest_first():
    many = [_inst(f"p{i}", f"P{i}", "202710", ["Fall 2026"]) for i in range(12)]
    recent = _with_history({**HISTORY, "instructors": many})["history"]["recentInstructors"]
    assert [r["slug"] for r in recent] == [f"p{i}" for i in range(10)]


def test_one_professor_under_two_banner_names_gets_one_card():
    history = {**HISTORY, "instructors": [
        _inst("olin-guha", "Olin Guha", "202710", ["Fall 2026"], 40),
        _inst("olin-guha", "Olin Guha", "202630", ["Spring 2026"], 90),
    ]}
    data = _with_history(history)
    assert [r["slug"] for r in data["history"]["recentInstructors"]] == ["olin-guha"]
    olin = next(p for p in data["professors"] if p["slug"] == "olin-guha")
    assert (olin["lastTerm"], olin["avgClassSize"]) == ("Fall 2026", 40)


def test_no_banner_terms_means_no_recent_instructors():
    h = _with_history(newest=None)["history"]
    assert h is not None and h["recentInstructors"] == []


def test_professor_rows_gain_banner_last_term_and_class_size():
    history = {**HISTORY, "instructors": [HISTORY["instructors"][0]]}
    by_slug = {p["slug"]: p for p in _with_history(history)["professors"]}
    assert (by_slug["olin-guha"]["lastTerm"], by_slug["olin-guha"]["avgClassSize"]) == ("Fall 2026", 40)
    assert (by_slug["amal-ahmed"]["lastTerm"], by_slug["amal-ahmed"]["avgClassSize"]) == (None, None)


def test_course_unknown_to_banner_has_no_history():
    db = FakeDB()
    data = build_course("CS3500", db.query, db.query_one, history_for=lambda c, q, qo: None)
    assert data["history"] is None
    assert not any("banner_terms" in s for s in db.calls)


def test_history_failure_still_builds_the_page(caplog):
    def broken(code, query, query_one):
        raise psycopg2.errors.UndefinedTable('relation "banner_course_offerings" does not exist')

    db = FakeDB()
    data = build_course("CS3500", db.query, db.query_one, history_for=broken)
    assert data["history"] is None
    assert [p["slug"] for p in data["professors"]] == ["olin-guha", "amal-ahmed"]
    assert all(p["lastTerm"] is None for p in data["professors"])
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1


class _FailingConn:
    closed = False

    def __init__(self):
        self.rolled_back = False

    def cursor(self, cursor_factory=None):
        return self

    def execute(self, sql, params):
        raise psycopg2.errors.UndefinedTable('relation "banner_terms" does not exist')

    def rollback(self):
        self.rolled_back = True


def test_a_failed_statement_rolls_back_so_the_requests_next_query_runs(monkeypatch):
    conn = _FailingConn()
    monkeypatch.setattr(server, "get_db", lambda: conn)
    with pytest.raises(psycopg2.errors.UndefinedTable):
        server.query("SELECT max(term_code) AS code FROM banner_terms")
    assert conn.rolled_back


@pytest.fixture
def client(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(server, "_get_pool",
                        lambda: (_ for _ in ()).throw(AssertionError("no DB in test")), raising=False)
    monkeypatch.setattr(server, "cache_get", lambda key: None, raising=False)
    monkeypatch.setattr(server, "cache_set", lambda key, data: None, raising=False)
    monkeypatch.setattr(server.limiter, "enabled", False)
    monkeypatch.setattr(server, "query", db.query, raising=False)
    monkeypatch.setattr(server, "query_one", db.query_one, raising=False)
    return server.app.test_client()


def test_route_serves_the_contract_case_insensitively(client):
    resp = client.get("/api/courses/cs3500")
    assert resp.status_code == 200
    assert resp.get_json()["code"] == "CS3500"
    assert "Authorization" in [t.strip() for t in resp.headers["Vary"].split(",")]


def test_route_404s_only_for_codes_outside_the_catalog(client):
    resp = client.get("/api/courses/zzzz9999")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Course not found"}


def test_courses_catalog_sends_no_rating_filter_unless_asked(monkeypatch):
    seen = []
    monkeypatch.setattr(server, "cache_get", lambda key: None, raising=False)
    monkeypatch.setattr(server, "cache_set", lambda key, data: None, raising=False)
    monkeypatch.setattr(server.limiter, "enabled", False)
    monkeypatch.setattr(server, "query_one", lambda sql, p=(): seen.append(sql) or {"cnt": 1}, raising=False)
    monkeypatch.setattr(server, "query", lambda sql, p=(): seen.append(sql) or [
        {"code": "CS3500", "name": "OOD", "department": "CS", "avg_rating": None}], raising=False)
    resp = server.app.test_client().get("/api/courses-catalog?sort=rating")
    assert resp.status_code == 200
    assert resp.get_json()["courses"][0]["avgRating"] is None
    assert not any("avg_rating >=" in s for s in seen)
    assert any("avg_rating DESC NULLS LAST, lower(code) ASC" in s for s in seen)
