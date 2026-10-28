"""review_ratings' merge against the real one-per-device unique index."""

import uuid

import psycopg2.extras
import pytest

import review_ratings
from tests.pg.pgutil import rows, truncate


@pytest.fixture(autouse=True)
def clean(pg):
    truncate(pg, "survey_ratings", "survey_pending_professors", "professors")


def insert(pg, slug, pending, updated_at, rating):
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO survey_ratings (id, professor_slug, pending_professor_id, "
            "course_code, rating, hours, status, device_hash, new_device, network_type, "
            "updated_at) VALUES (%s, %s, %s, 'CS2500', %s, '3-4', 'accepted', 'd1', "
            "true, 'unknown', %s)",
            (str(uuid.uuid4()), slug, pending, rating, updated_at))


def test_merge_professor_against_the_real_index(pg):
    pending = str(uuid.uuid4())
    with pg.cursor() as cur:
        cur.execute("INSERT INTO professors (slug, name, name_key) "
                    "VALUES ('jane-doe', 'Jane Doe', 'jane doe')")
        cur.execute("INSERT INTO survey_pending_professors (id, name, name_key) "
                    "VALUES (%s, 'J Doe', 'j doe')", (pending,))
    insert(pg, "jane-doe", None, "2026-10-01T00:00:00Z", 2)
    insert(pg, None, pending, "2026-10-10T00:00:00Z", 5)
    pg.commit()

    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        review_ratings.merge_professor(cur, pending, "jane-doe")
    pg.commit()

    left = rows(pg, "SELECT professor_slug, pending_professor_id, rating "
                    "FROM survey_ratings WHERE device_hash = 'd1' AND course_code = 'CS2500'")
    assert left == [{"professor_slug": "jane-doe", "pending_professor_id": None, "rating": 5}]


def test_merge_professor_keeps_the_existing_rating_when_it_is_newer(pg):
    pending = str(uuid.uuid4())
    with pg.cursor() as cur:
        cur.execute("INSERT INTO professors (slug, name, name_key) "
                    "VALUES ('jane-doe', 'Jane Doe', 'jane doe')")
        cur.execute("INSERT INTO survey_pending_professors (id, name, name_key) "
                    "VALUES (%s, 'J Doe', 'j doe')", (pending,))
    insert(pg, "jane-doe", None, "2026-10-10T00:00:00Z", 4)
    insert(pg, None, pending, "2026-10-01T00:00:00Z", 1)
    pg.commit()

    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        review_ratings.merge_professor(cur, pending, "jane-doe")
    pg.commit()

    left = rows(pg, "SELECT professor_slug, pending_professor_id, rating "
                    "FROM survey_ratings WHERE device_hash = 'd1' AND course_code = 'CS2500'")
    assert left == [{"professor_slug": "jane-doe", "pending_professor_id": None, "rating": 4}]


@pytest.mark.parametrize("before, after", [(None, "comment_removed"),
                                           ("too_fast", "too_fast,comment_removed"),
                                           ("comment_removed", "comment_removed")])
def test_remove_comment_marks_the_row_and_keeps_its_hash(pg, monkeypatch, before, after):
    import survey_reviews
    secret = "s" * 32
    monkeypatch.setenv("RATING_SECRET", secret)
    rating_id = str(uuid.uuid4())
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO survey_ratings (id, professor_slug, course_code, rating, hours, comment, comment_hash, "
            "status, hold_reasons, device_hash, new_device, network_type) "
            "VALUES (%s, 'jane-doe', 'CS2500', 4, '3-4', 'rude', 'h1', 'accepted', %s, 'd1', true, 'unknown')",
            (rating_id, before))
    pg.commit()

    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        review_ratings.remove_comment(cur, survey_reviews.public_id(secret, rating_id))
    pg.commit()

    assert rows(pg, "SELECT comment, comment_hash, status, hold_reasons FROM survey_ratings") == [
        {"comment": None, "comment_hash": "h1", "status": "accepted", "hold_reasons": after}]
