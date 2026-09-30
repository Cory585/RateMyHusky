"""precompute end to end against a mocked connection, and its pure helpers.

The whole build runs from tiny RMP CSVs in a tmp dir; the connection records
every statement and rows inserted, so nothing here reaches a database.
"""

import pathlib
import re

import pandas as pd
import pytest

import precompute
from precompute import CATALOG_COLUMNS, assign_slugs, apply_avg_rating

PRECOMPUTE_PY = pathlib.Path(precompute.__file__)


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.db.statements.append((sql, params))
        self._result = []
        if "FROM information_schema.tables" in sql:
            name = params[0]
            self._result = [(1,)] if name in self.db.tables else []
        elif sql.startswith("SELECT") and " FROM " in sql:
            table = re.search(r"FROM (\w+)", sql).group(1)
            self._result = self.db.tables.get(table, [])

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def close(self):
        pass


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class FakeDB:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.statements = []
        self.inserts = {}

    def sql(self):
        return "\n".join(s for s, _ in self.statements)


def _write_csvs(tmp_path):
    pd.DataFrame([
        {"name": "Ann Lee", "department": "Computer Science", "rating": 4.0, "num_ratings": 9,
         "level_of_difficulty": 3.0, "would_take_again_pct": "80%", "professor_url": "http://x/1"},
        {"name": "Bob Ray", "department": "Math", "rating": 3.0, "num_ratings": 1,
         "level_of_difficulty": 2.0, "would_take_again_pct": "N/A", "professor_url": ""},
        {"name": "Cy Zed", "department": "Math", "rating": 5.0, "num_ratings": 0,
         "level_of_difficulty": 0, "would_take_again_pct": "N/A", "professor_url": ""},
    ]).to_csv(tmp_path / "rmp_professors.csv", index=False)
    pd.DataFrame([
        {"professor_name": "Ann Lee", "department": "Computer Science", "course": "cs 2500",
         "quality": 5, "difficulty": 3, "comment": "great"},
        {"professor_name": "Ann Lee", "department": "Computer Science", "course": "2500",
         "quality": 3, "difficulty": 3, "comment": ""},
        {"professor_name": "Ann Lee", "department": "Computer Science", "course": "junk",
         "quality": 4, "difficulty": 3, "comment": "fine"},
        {"professor_name": "Bob Ray", "department": "Math", "course": "MATH1341",
         "quality": 2, "difficulty": 4, "comment": None},
    ]).to_csv(tmp_path / "rmp_reviews.csv", index=False)
    pd.DataFrame(
        [{"name": "Ann Lee", "image_url": "http://img/ann-100x100.jpg"}]
    ).to_csv(tmp_path / "professor_photos.csv", index=False)


@pytest.fixture
def build(tmp_path, monkeypatch):
    _write_csvs(tmp_path)

    def run(tables=None):
        db = FakeDB(tables)
        monkeypatch.setattr(precompute, "_connect", lambda: FakeConn(db))
        monkeypatch.setattr(precompute, "swap_in", lambda conn, table: db.statements.append((f"SWAP {table}", None)))
        monkeypatch.setattr(precompute, "denied_hashes", lambda: set())
        monkeypatch.setattr(
            precompute, "execute_values",
            lambda cur, sql, rows: db.inserts.setdefault(
                re.search(r"INSERT INTO (\w+)", sql).group(1), []).extend(rows))
        precompute.main(csv_dir=str(tmp_path))
        return db
    return run


def test_build_runs_end_to_end_and_writes_the_contract_tables(build):
    db = build()
    assert len(db.inserts["professors_catalog_new"]) == 3
    assert {r[0] for r in db.inserts["course_catalog_new"]} == {"CS2500", "MATH1341"}
    sql = db.sql()
    assert "SWAP professors_catalog" in sql and "SWAP course_catalog" in sql
    assert "idx_rr_course_code" in sql
    assert "ADD COLUMN course_code" in sql


def test_catalog_columns_match_the_insert(build):
    db = build()
    src = PRECOMPUTE_PY.read_text()
    ddl = re.search(r"CREATE TABLE professors_catalog_new \((.*?)\n\s*\)\n", src, re.S).group(1)
    ddl_cols = [l.split()[0] for l in (x.strip() for x in ddl.strip().splitlines()) if l]
    ins = re.search(r"INSERT INTO professors_catalog_new\s*\n\s*\((.*?)\)\s*\n\s*VALUES", src, re.S).group(1)
    ins_cols = [c.strip() for c in ins.replace("\n", " ").split(",")]
    assert list(CATALOG_COLUMNS) == ddl_cols == ins_cols
    assert all(len(r) == len(CATALOG_COLUMNS) for r in db.inserts["professors_catalog_new"])


def test_no_removed_columns_remain(build):
    build()
    src = PRECOMPUTE_PY.read_text().lower()
    for word in ("trace", "avg_hours", "is_topics", "num_responses"):
        assert word not in src


def test_rating_review_and_comment_definitions(build):
    db = build()
    rows = {r[2]: dict(zip(CATALOG_COLUMNS, r)) for r in db.inserts["professors_catalog_new"]}
    ann = rows["ann lee"]
    # rating = mean of stored quality (5, 3, 4), count = rows held, comments = non-empty text.
    assert ann["rmp_rating"] == 4.0 and ann["avg_rating"] == 4.0
    assert ann["num_ratings"] == 3 and ann["total_reviews"] == 3
    assert ann["total_comments"] == 2
    assert ann["image_url"] == "http://img/ann.jpg"
    assert rows["bob ray"]["total_comments"] == 0
    # No reviews held: no rating shown, however RMP's stale summary reads.
    assert rows["cy zed"]["num_ratings"] == 0
    assert rows["cy zed"]["avg_rating"] is None and rows["cy zed"]["rmp_rating"] is None


def test_course_catalog_rows_and_bare_number_resolution(build):
    db = build()
    by = {r[0]: r for r in db.inserts["course_catalog_new"]}
    # "cs 2500" and the bare "2500" both land on CS2500; "junk" resolves nowhere.
    assert by["CS2500"][4] == 4.0 and by["CS2500"][5] == 2
    assert by["CS2500"][1] is None and by["CS2500"][2] == "Computer Science"


def test_course_catalog_prefers_catalog_courses_then_previous(build):
    db = build({
        "catalog_courses": [("CS2500", "Fundamentals of CS 1", "Khoury")],
        "course_catalog": [("MATH1341", "Calculus 1", "Mathematics")],
    })
    by = {r[0]: r for r in db.inserts["course_catalog_new"]}
    assert by["CS2500"][1:3] == ("Fundamentals of CS 1", "Khoury")
    assert by["MATH1341"][1:3] == ("Calculus 1", "Mathematics")


def test_existing_slugs_are_preserved_by_name_key(build):
    db = build({"professors_catalog": [("ann lee", "ann-lee-old")]})
    slugs = {r[2]: r[0] for r in db.inserts["professors_catalog_new"]}
    assert slugs["ann lee"] == "ann-lee-old"
    assert slugs["bob ray"] == "bob-ray"


def test_stats_are_written(build):
    db = build()
    upsert = [p for s, p in db.statements if s.startswith("UPSERT INTO stats_cache")][0]
    assert upsert == (3, 2, 4, 2)


# ── assign_slugs ────────────────────────────────────────────────────────────

def test_preserved_slugs_are_reserved_before_new_ones():
    # "a b" is new and would take "a-b", which "a  b" already owns.
    slugs = assign_slugs(["a b", "a  b"], {"a  b": "a-b"})
    assert slugs == ["a-b-2", "a-b"]


def test_colliding_new_names_get_suffixes():
    assert assign_slugs(["x y", "x-y"], {}) == ["x-y", "x-y-2"]


# ── apply_avg_rating ────────────────────────────────────────────────────────

def test_avg_rating_is_the_rmp_rating_or_null():
    df = pd.DataFrame({"rating": [4.256, 3.0, 4.0], "num_ratings": [5, 0, 2]})
    apply_avg_rating(df)
    assert df["avg_rating"].iloc[0] == 4.26
    assert pd.isna(df["avg_rating"].iloc[1])
    assert df["avg_rating"].iloc[2] == 4.0
