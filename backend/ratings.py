"""Injectable SQL layer for the no-login rating form.

Pure functions that take `query`/`query_one` (read) and `write` (execute+commit,
never fetches) so they can be unit-tested without a live DB. server.py wires
these to the real helpers and owns all HTTP concerns. Every `now` is a
timezone-aware UTC datetime.
"""

import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

import rmp

# The burst thresholds count prior ratings in the window: the route counts before it saves.
PAIR_BURST = 5  # prior new-device ratings for one professor + course in an hour
PAIR_PACE_FACTOR = 3  # ...and this many times the pair's 30-day mean hourly count
IP_BURST = 10  # prior ratings from one IP hash in an hour
PREFIX_BURST = 25  # prior ratings from one /24 or /64 hash in an hour
COUNTER_MIN = 25  # the "huskies this term" counter is hidden below this
IP_HASH_DAYS = 30
TERM_START_MONTHS = (1, 5, 9)

# Every survey_ratings column except created_at / updated_at.
RATING_COLUMNS = (
    "id", "professor_slug", "pending_professor_id", "course_code",
    "pending_course_id", "term_label", "rating", "hours", "grade", "comment",
    "comment_hash", "status", "hold_reasons", "device_hash", "new_device",
    "ip_hash", "ip_prefix_hash", "network_type", "fill_seconds", "collection",
)

_KEY_COLUMNS = ("professor_slug", "pending_professor_id", "course_code", "pending_course_id")
_KEY_MATCH = " AND ".join(f"{c} IS NOT DISTINCT FROM %s" for c in _KEY_COLUMNS)
_NO_NETWORK_BURST = ("campus", "relay")
_BURST_REASONS = ("burst_pair", "burst_ip", "burst_prefix")


def _normalize_name(name):
    s = str(name).strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip()


def term_start(now):
    """The latest Jan 1 / May 1 / Sep 1 00:00 UTC at or before `now`."""
    month = max(m for m in TERM_START_MONTHS if m <= now.month)
    return datetime(now.year, month, 1, tzinfo=timezone.utc)


def huskies_this_term(query_one, now):
    """Ratings submitted this term (accepted and held), or None below COUNTER_MIN."""
    row = query_one("SELECT count(*) AS cnt FROM survey_ratings WHERE created_at >= %s",
                    (term_start(now),))
    count = row["cnt"] if row else 0
    return count if count >= COUNTER_MIN else None


def professor_exists(slug, query_one):
    return query_one("SELECT 1 FROM professors_catalog WHERE slug = %s", (slug,)) is not None


def course_exists(code, query_one):
    if query_one("SELECT 1 FROM course_catalog WHERE code = %s", (code,)) is not None:
        return True
    return query_one("SELECT 1 FROM banner_course_offerings WHERE subject_course = %s",
                     (code,)) is not None


def pending_professor(name, department, query_one, write):
    """(id, created): reuse a pending row with the same normalized name, else insert."""
    name_key = _normalize_name(name)
    row = query_one(
        "SELECT id FROM survey_pending_professors WHERE name_key = %s AND status = 'pending'",
        (name_key,))
    if row:
        return str(row["id"]), False
    new_id = str(uuid.uuid4())
    write("INSERT INTO survey_pending_professors (id, name, name_key, department) "
          "VALUES (%s, %s, %s, %s)", (new_id, name, name_key, department))
    return new_id, True


def pending_course(code, name, query_one, write):
    """(id, created): reuse a pending row with the same code, else insert."""
    row = query_one(
        "SELECT id FROM survey_pending_courses WHERE code = %s AND status = 'pending'", (code,))
    if row:
        return str(row["id"]), False
    new_id = str(uuid.uuid4())
    write("INSERT INTO survey_pending_courses (id, code, name) VALUES (%s, %s, %s)",
          (new_id, code, name))
    return new_id, True


def rating_key(clean, pending_professor_id, pending_course_id):
    """The four columns that, with the device, identify one rating."""
    return {
        "professor_slug": None if pending_professor_id else clean.get("professor_slug"),
        "pending_professor_id": pending_professor_id,
        "course_code": None if pending_course_id else clean.get("course_code"),
        "pending_course_id": pending_course_id,
    }


def _key_params(key):
    return tuple(key[c] for c in _KEY_COLUMNS)


def existing_rating(device_hash, key, query_one):
    return query_one(
        f"SELECT id, status FROM survey_ratings WHERE device_hash = %s AND {_KEY_MATCH}",
        (device_hash, *_key_params(key)))


def device_seen(device_hash, query_one):
    """True when this device already has a rating, so its token isn't one a client made up."""
    return query_one("SELECT 1 FROM survey_ratings WHERE device_hash = %s LIMIT 1", (device_hash,)) is not None


def duplicate_comment(comment_hash, exclude_id, query_one):
    """True when another rating already carries this comment hash."""
    if comment_hash is None:
        return False
    row = query_one(
        "SELECT 1 FROM survey_ratings WHERE comment_hash = %s AND id IS DISTINCT FROM %s LIMIT 1",
        (comment_hash, exclude_id))
    return row is not None


def burst_reasons(key, ip_hash, prefix_hash, network_type, query_one, now):
    """Which of burst_pair / burst_ip / burst_prefix this submission trips."""
    hour_ago = now - timedelta(hours=1)
    reasons = []
    row = query_one(
        "SELECT count(*) FILTER (WHERE created_at >= %s) AS hour, count(*) AS month "
        f"FROM survey_ratings WHERE new_device = true AND created_at >= %s AND {_KEY_MATCH}",
        (hour_ago, now - timedelta(days=30), *_key_params(key)))
    if row:
        pace = row["month"] / 720
        if row["hour"] >= PAIR_BURST and row["hour"] >= PAIR_PACE_FACTOR * pace:
            reasons.append("burst_pair")
    if network_type not in _NO_NETWORK_BURST:
        for reason, column, value, limit in (
            ("burst_ip", "ip_hash", ip_hash, IP_BURST),
            ("burst_prefix", "ip_prefix_hash", prefix_hash, PREFIX_BURST),
        ):
            if value is None:
                continue
            row = query_one(
                f"SELECT count(*) AS cnt FROM survey_ratings WHERE {column} = %s AND created_at >= %s",
                (value, hour_ago))
            if row and row["cnt"] >= limit:
                reasons.append(reason)
    return reasons


def hold_burst(reasons, key, ip_hash, prefix_hash, write, now):
    """Hold the accepted rows from the last hour that match each tripped trigger."""
    hour_ago = now - timedelta(hours=1)
    where = {
        "burst_pair": (_KEY_MATCH, _key_params(key)),
        "burst_ip": ("ip_hash = %s", (ip_hash,)),
        "burst_prefix": ("ip_prefix_hash = %s", (prefix_hash,)),
    }
    for reason in _BURST_REASONS:
        if reason not in reasons:
            continue
        condition, params = where[reason]
        write(
            "UPDATE survey_ratings SET status = 'held', hold_reasons = "
            "CASE WHEN hold_reasons IS NULL OR hold_reasons = '' THEN %s "
            "ELSE hold_reasons || ',' || %s END "
            f"WHERE status = 'accepted' AND created_at >= %s AND {condition}",
            (reason, reason, hour_ago, *params))


# The conflict target (device_hash + the four key columns) is never updated;
# status and hold_reasons have their own held-stays-held rules below.
_INSERT_COLUMNS = tuple(c for c in RATING_COLUMNS if c != "id")
_SET_COLUMNS = tuple(
    c for c in _INSERT_COLUMNS
    if c not in ("status", "hold_reasons", "device_hash", *_KEY_COLUMNS))
_UPSERT_SQL = (
    f"INSERT INTO survey_ratings (id, {', '.join(_INSERT_COLUMNS)}) "
    f"VALUES (%s, {', '.join(['%s'] * len(_INSERT_COLUMNS))}) "
    "ON CONFLICT (device_hash, professor_slug, pending_professor_id, course_code, "
    "pending_course_id) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _SET_COLUMNS)
    + ", status = CASE WHEN survey_ratings.status = 'held' THEN 'held' ELSE EXCLUDED.status END"
    ", hold_reasons = CASE WHEN survey_ratings.status = 'held' AND EXCLUDED.hold_reasons IS NULL "
    "THEN survey_ratings.hold_reasons ELSE EXCLUDED.hold_reasons END"
    ", updated_at = now()"
)


def save_rating(row, existing, write):
    """Insert or replace this device's rating in one statement; return its id.

    The unique index on (device, key) is the conflict target, so two racing
    submits still leave one row, and a held row stays held.
    """
    new_id = str(uuid.uuid4())
    write(_UPSERT_SQL, (new_id, *(row[c] for c in _INSERT_COLUMNS)))
    return existing["id"] if existing else new_id


def professor_context(slug, device_hash, query):
    """The form's left panel: courses this device rated, and per-course blend stats."""
    rated = []
    if device_hash is not None:
        rows = query(
            "SELECT course_code FROM survey_ratings WHERE device_hash = %s "
            "AND professor_slug = %s AND course_code IS NOT NULL ORDER BY course_code",
            (device_hash, slug))
        rated = [r["course_code"] for r in rows]
    rows = query(
        "SELECT course_code, source, rating, num_ratings, hours_per_week, grade_distribution "
        "FROM source_summary WHERE professor_slug = %s AND course_code <> '' "
        "AND source IN ('blend', 'survey')", (slug,))
    survey_counts = {r["course_code"]: int(r["num_ratings"] or 0)
                     for r in rows if r["source"] == "survey"}
    summaries = {}
    for r in rows:
        if r["source"] != "blend":
            continue
        grades = r.get("grade_distribution") or {}
        summaries[r["course_code"]] = {
            "rating": rmp.stat(r["rating"], 2),
            "numRatings": int(r["num_ratings"] or 0),
            "hoursPerWeek": rmp.stat(r["hours_per_week"], 1),
            "hoursCount": survey_counts.get(r["course_code"], 0),
            "gradeDistribution": grades,
            "gradeCount": sum(grades.values()),
        }
    return {"ratedCourses": rated, "summaries": summaries}


def purge_ip_hashes(write, now):
    """Clear IP and prefix hashes once they are older than IP_HASH_DAYS."""
    write("UPDATE survey_ratings SET ip_hash = NULL, ip_prefix_hash = NULL "
          "WHERE created_at < %s AND ip_hash IS NOT NULL",
          (now - timedelta(days=IP_HASH_DAYS),))
