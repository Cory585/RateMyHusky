"""The survey rating tables' unique index and CHECKs against real PostgreSQL."""

import uuid

import psycopg2
import pytest

from tests.pg.pgutil import rows, scalar, truncate


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


def pg_write(pg):
    def write(sql, params):
        with pg.cursor() as cur:
            cur.execute(sql, params)
        pg.commit()
    return write


def pg_query_one(pg):
    def query_one(sql, params):
        found = rows(pg, sql, params)
        return found[0] if found else None
    return query_one


def test_pending_rows_are_one_per_key_against_the_real_index(pg):
    import ratings

    truncate(pg, "survey_pending_professors", "survey_pending_courses")
    write, query_one = pg_write(pg), pg_query_one(pg)
    first = ratings.pending_professor("Jane Doe", None, query_one, write)
    assert first[1] is True
    assert ratings.pending_professor("jane  DOE", None, query_one, write) == (first[0], False)
    first = ratings.pending_course("ZZ9999", "New", query_one, write)
    assert ratings.pending_course("ZZ9999", "Other", query_one, write) == (first[0], False)
    assert scalar(pg, "SELECT count(*) FROM survey_pending_professors") == 1
    assert scalar(pg, "SELECT count(*) FROM survey_pending_courses") == 1
    # A resolved row no longer blocks a new pending one for the same code.
    with pg.cursor() as cur:
        cur.execute("UPDATE survey_pending_courses SET status = 'approved'")
    pg.commit()
    assert ratings.pending_course("ZZ9999", "New", query_one, write)[1] is True


def test_upsert_keeps_new_device_and_merges_hold_reasons(pg):
    import ratings

    insert(pg, "d1", "jane-doe", None, "CS2500", None, status="held")
    with pg.cursor() as cur:
        cur.execute("UPDATE survey_ratings SET hold_reasons = 'burst_ip,duplicate_comment'")
    pg.commit()
    row = {c: None for c in ratings.RATING_COLUMNS if c != "id"}
    row.update(professor_slug="jane-doe", course_code="CS2500", rating=5, hours="3-4",
               status="held", hold_reasons="too_fast", device_hash="d1", new_device=False,
               network_type="unknown", collection="turnstile+device")
    key = ratings.rating_key(row, None, None)
    existing = ratings.existing_rating("d1", key, pg_query_one(pg))
    ratings.save_rating(row, existing, pg_write(pg))
    (saved,) = rows(pg, "SELECT new_device, hold_reasons FROM survey_ratings")
    assert saved == {"new_device": True, "hold_reasons": "burst_ip,duplicate_comment,too_fast"}


def test_hold_burst_skips_returning_devices_and_approved_rows(pg):
    import ratings
    from datetime import datetime, timezone

    insert(pg, "d1", "jane-doe", None, "CS2500", None)  # new device
    insert(pg, "d2", "jane-doe", None, "CS2500", None)  # returning device
    insert(pg, "d3", "jane-doe", None, "CS2500", None)  # approved by a reviewer
    with pg.cursor() as cur:
        cur.execute("UPDATE survey_ratings SET new_device = false WHERE device_hash = 'd2'")
        cur.execute("UPDATE survey_ratings SET hold_reasons = 'burst_pair' WHERE device_hash = 'd3'")
    pg.commit()
    key = {"professor_slug": "jane-doe", "pending_professor_id": None,
           "course_code": "CS2500", "pending_course_id": None}
    ratings.hold_burst(["burst_pair"], key, None, None, pg_write(pg), datetime.now(timezone.utc))
    held = rows(pg, "SELECT device_hash FROM survey_ratings WHERE status = 'held'")
    assert [r["device_hash"] for r in held] == ["d1"]


def test_burst_queries_run_and_a_held_burst_pauses_its_scope(pg):
    import ratings
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    key = {"professor_slug": "jane-doe", "pending_professor_id": None,
           "course_code": "CS2500", "pending_course_id": None}
    query_one = pg_query_one(pg)
    assert ratings.burst_reasons(key, "ip", "pre", "other", query_one, now) == []
    # A pair burst on another course pauses only that pair.
    insert(pg, "d1", "jane-doe", None, "CS3500", None, status="held")
    with pg.cursor() as cur:
        cur.execute("UPDATE survey_ratings SET hold_reasons = 'no_timer,burst_pair'")
    pg.commit()
    assert ratings.burst_reasons(key, "ip", "pre", "other", query_one, now) == []
    # A professor burst pauses every course of theirs, until a reviewer clears it.
    with pg.cursor() as cur:
        cur.execute("UPDATE survey_ratings SET hold_reasons = 'no_timer,burst_prof'")
    pg.commit()
    assert ratings.burst_reasons(key, "ip", "pre", "other", query_one, now) == ["burst_pause"]
    with pg.cursor() as cur:
        cur.execute("UPDATE survey_ratings SET status = 'accepted'")
    pg.commit()
    assert ratings.burst_reasons(key, "ip", "pre", "other", query_one, now) == []


def test_pair_taught_reads_the_real_banner_table(pg):
    import ratings

    truncate(pg, "banner_course_instructors")
    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO banner_course_instructors (subject_course, instructor_key, professor_slug, "
            "match_method, terms_taught, sections, first_term_code, last_term_code, last_term_label) "
            "VALUES ('CS2500', 'k1', 'jane-doe', 'exact', 1, 1, '202510', '202510', 'Fall 2024')")
    pg.commit()
    assert ratings.pair_taught("jane-doe", "CS2500", pg_query_one(pg))
    assert not ratings.pair_taught("jane-doe", "CS3500", pg_query_one(pg))
