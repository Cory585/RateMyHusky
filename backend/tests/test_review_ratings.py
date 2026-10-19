"""Unit tests for the review_ratings CLI, using a recording fake cursor."""

import pytest

import review_ratings as rr

PENDING = {"id": "p1", "name": "Jane Doe", "name_key": "jane doe",
           "department": "Computer Science", "status": "pending"}


class FakeCursor:
    """Records execute(sql, params); fetchone/fetchall serve the first canned
    result whose key is a substring of the SQL (an empty list when none match)."""

    def __init__(self, canned=None):
        self.canned = canned or {}
        self.calls = []
        self._rows = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._rows = next((rows for key, rows in self.canned.items() if key in sql), [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def statements(self, prefix):
        return [(s, p) for s, p in self.calls if s.lstrip().startswith(prefix)]


def index_of(cur, fragment):
    return next(i for i, (sql, _) in enumerate(cur.calls) if fragment in sql)


def test_approve_professor_inserts_survey_origin_and_moves_ratings():
    cur = FakeCursor({"FROM survey_pending_professors": [PENDING],
                      "ORDER BY slug": [{"slug": "john-doe"}]})
    summary = rr.approve_professor(cur, "p1")
    (sql, params), = cur.statements("INSERT INTO professors")
    assert "'survey'" in sql
    assert params[:4] == ("jane-doe", "Jane Doe", "jane doe", "Computer Science")
    (sql, params), = cur.statements("UPDATE survey_ratings")
    assert "professor_slug = %s" in sql and "pending_professor_id = NULL" in sql
    assert params == ("jane-doe", "p1")
    (sql, params), = cur.statements("UPDATE survey_pending_professors")
    assert "status = 'approved'" in sql and params == ("jane-doe", "p1")
    assert "jane-doe" in summary


def test_approve_professor_twice_raises():
    cur = FakeCursor({"FROM survey_pending_professors": [dict(PENDING, status="approved")]})
    with pytest.raises(rr.ReviewError):
        rr.approve_professor(cur, "p1")
    assert not cur.statements("INSERT") and not cur.statements("UPDATE")


def test_approve_professor_refuses_an_existing_name_key():
    cur = FakeCursor({"FROM survey_pending_professors": [PENDING],
                      "WHERE name_key": [{"slug": "jane-doe"}]})
    with pytest.raises(rr.ReviewError, match="jane-doe"):
        rr.approve_professor(cur, "p1")
    assert not cur.statements("INSERT")


def test_approve_professor_strips_control_characters_from_the_name():
    evil = dict(PENDING, name="Jane[2J Doe")
    cur = FakeCursor({"FROM survey_pending_professors": [evil],
                      "ORDER BY slug": [{"slug": "john-doe"}]})
    assert "" not in rr.approve_professor(cur, "p1")
    cur = FakeCursor({"FROM survey_pending_professors": [evil],
                      "WHERE name_key": [{"slug": "jane-doe"}]})
    with pytest.raises(rr.ReviewError) as e:
        rr.approve_professor(cur, "p1")
    assert "" not in str(e.value)


def test_approve_course_strips_control_characters_from_the_code():
    row = {"id": "c1", "code": "CS[2J1", "name": "X", "status": "pending"}
    cur = FakeCursor({"FROM survey_pending_courses": [row]})
    assert "" not in rr.approve_course(cur, "c1")


@pytest.mark.parametrize("slug", ["Jane-Doe", "jane doe", "jane--doe", "-jane", "jane-", "jane/doe", ""])
def test_approve_professor_rejects_a_malformed_slug(slug):
    cur = FakeCursor({"FROM survey_pending_professors": [PENDING]})
    with pytest.raises(rr.ReviewError, match="slug"):
        rr.approve_professor(cur, "p1", slug)
    assert not cur.statements("INSERT")


def test_merge_keeps_the_newest_rating_per_device_and_course():
    cur = FakeCursor({"FROM survey_pending_professors": [PENDING],
                      "SELECT 1 FROM professors": [{"?column?": 1}]})
    rr.merge_professor(cur, "p1", "jane-doe")
    delete = index_of(cur, "DELETE FROM survey_ratings")
    move = index_of(cur, "UPDATE survey_ratings")
    assert delete < move
    sql = cur.calls[delete][0]
    assert "IS NOT DISTINCT FROM" in sql and "d.updated_at" in sql
    assert "status = 'merged'" in cur.calls[index_of(cur, "UPDATE survey_pending_professors")][0]


def test_merge_into_an_unknown_slug_raises():
    cur = FakeCursor({"FROM survey_pending_professors": [PENDING]})
    with pytest.raises(rr.ReviewError):
        rr.merge_professor(cur, "p1", "nobody")
    assert not cur.statements("DELETE") and not cur.statements("UPDATE")


def test_reject_professor_deletes_only_its_ratings():
    cur = FakeCursor({"FROM survey_pending_professors": [PENDING]})
    rr.reject_professor(cur, "p1")
    (sql, params), = cur.statements("DELETE")
    assert sql.split("WHERE")[1].strip() == "pending_professor_id = %s"
    assert params == ("p1",)
    assert "status = 'rejected'" in cur.statements("UPDATE")[0][0]


def test_approve_course_moves_ratings_onto_the_pending_code():
    course = {"id": "c1", "code": "CS9999", "name": "Topics", "status": "pending"}
    cur = FakeCursor({"FROM survey_pending_courses": [course]})
    rr.approve_course(cur, "c1")
    assert index_of(cur, "DELETE FROM survey_ratings") < index_of(cur, "UPDATE survey_ratings")
    (sql, params), = cur.statements("UPDATE survey_ratings")
    assert "course_code = %s" in sql and "pending_course_id = NULL" in sql
    assert params == ("CS9999", "c1")


def test_approve_rating_only_touches_held_rows():
    cur = FakeCursor({"UPDATE survey_ratings": [{"id": "abcd1234"}]})
    rr.approve_rating(cur, "abcd1234")
    (sql, params), = cur.calls
    assert "status = 'held'" in sql and "'accepted'" in sql
    assert "updated_at" not in sql
    assert params == ("abcd1234%",)
    with pytest.raises(rr.ReviewError):
        rr.approve_rating(FakeCursor(), "abcd1234")


def test_list_prints_no_hashes(capsys):
    held = {"id": "abcd1234-0000", "professor": "jane-doe", "course": "CS2500",
            "term_label": "Fall 2025", "rating": 2, "hold_reasons": "burst",
            "comment": "x" * 100, "created_at": "2026-10-01",
            "device_hash": "DEVICEHASH", "ip_hash": "IPHASH"}
    cur = FakeCursor({"FROM survey_ratings": [held]})
    rr.list_pending(cur)
    out = capsys.readouterr().out
    assert "abcd1234" in out and "jane-doe" in out and "x" * 80 in out and "x" * 81 not in out
    assert "DEVICEHASH" not in out and "IPHASH" not in out


def test_reject_rating_only_deletes_held_rows():
    cur = FakeCursor({"DELETE FROM survey_ratings": [{"id": "abcd1234"}]})
    rr.reject_rating(cur, "abcd1234")
    assert "status = 'held'" in cur.calls[0][0]
    with pytest.raises(rr.ReviewError):
        rr.reject_rating(FakeCursor(), "abcd1234")


def test_list_strips_control_characters(capsys):
    evil = "[2J"
    held = {"id": "abcd1234-0000", "professor": "jane-doe", "course": "CS2500",
            "term_label": None, "rating": 2, "hold_reasons": "burst",
            "comment": "hi" + evil, "created_at": "2026-10-01"}
    cur = FakeCursor({"FROM survey_ratings": [held],
                      "FROM survey_pending_professors": [{"id": "p1", "name": "Eve" + evil,
                                                          "department": evil, "n": 1}],
                      "FROM survey_pending_courses": [{"id": "c1", "code": evil, "name": evil,
                                                       "n": 1, "in_catalog": False}]})
    rr.list_pending(cur)
    assert "" not in capsys.readouterr().out
