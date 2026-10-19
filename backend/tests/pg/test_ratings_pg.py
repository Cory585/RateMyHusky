"""The survey rating tables' unique index and CHECKs against real PostgreSQL."""

import uuid

import psycopg2
import pytest

from tests.pg.pgutil import scalar, truncate


@pytest.fixture(autouse=True)
def clean(pg):
    truncate(pg, "survey_ratings")


def insert(pg, device, slug, pending_prof, course, pending_course,
           rating=4, status="accepted"):
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO survey_ratings (id, professor_slug, pending_professor_id, "
            "course_code, pending_course_id, rating, hours, status, device_hash, "
            "new_device, network_type) "
            "VALUES (%s, %s, %s, %s, %s, %s, '3-4', %s, %s, true, 'unknown')",
            (str(uuid.uuid4()), slug, pending_prof, course, pending_course,
             rating, status, device))


def test_one_rating_per_device_and_key(pg):
    insert(pg, "d1", "jane-doe", None, "CS2500", None)
    pg.commit()
    with pytest.raises(psycopg2.errors.UniqueViolation):
        insert(pg, "d1", "jane-doe", None, "CS2500", None)
    pg.rollback()
    insert(pg, "d1", "jane-doe", None, "CS3500", None)
    pg.commit()
    assert scalar(pg, "SELECT count(*) FROM survey_ratings") == 2


@pytest.mark.parametrize("kwargs", [
    {"rating": 6},
    {"status": "published"},
    {"slug": None},
])
def test_survey_checks_reject_bad_rows(pg, kwargs):
    row = {"slug": "jane-doe", "rating": 4, "status": "accepted"}
    row.update(kwargs)
    with pytest.raises(psycopg2.errors.CheckViolation):
        insert(pg, "d1", row["slug"], None, "CS2500", None,
               rating=row["rating"], status=row["status"])
    pg.rollback()


def test_upsert_keeps_a_held_row_held(pg):
    import ratings

    insert(pg, "d1", "jane-doe", None, "CS2500", None, rating=2, status="held")
    pg.commit()
    row = {c: None for c in ratings.RATING_COLUMNS if c != "id"}
    row.update(professor_slug="jane-doe", course_code="CS2500", rating=5, hours="3-4",
               status="accepted", device_hash="d1", new_device=False,
               network_type="unknown", collection="turnstile+device")

    def write(sql, params):
        with pg.cursor() as cur:
            cur.execute(sql, params)
        pg.commit()

    ratings.save_rating(row, None, write)
    assert scalar(pg, "SELECT count(*) FROM survey_ratings") == 1
    assert scalar(pg, "SELECT status FROM survey_ratings") == "held"
    assert scalar(pg, "SELECT rating FROM survey_ratings") == 5
