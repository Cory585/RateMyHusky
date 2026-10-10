"""backup_db: pg_dump version gate and file naming."""

import datetime

import pytest

import backup_db


def test_pg_dump_major_parses_real_version_strings():
    assert backup_db.pg_dump_major("pg_dump (PostgreSQL) 16.4") == 16
    assert backup_db.pg_dump_major("pg_dump (PostgreSQL) 16.10 (Ubuntu 16.10-0ubuntu0.24.04.1)") == 16
    assert backup_db.pg_dump_major("pg_dump (PostgreSQL) 15.8") == 15
    assert backup_db.pg_dump_major("garbage") is None


def test_require_pg_dump_rejects_missing_and_old_versions():
    with pytest.raises(SystemExit, match="not found"):
        backup_db.require_pg_dump(which=lambda name: None)

    class Out:
        stdout = "pg_dump (PostgreSQL) 15.8\n"

    with pytest.raises(SystemExit, match="too old"):
        backup_db.require_pg_dump(which=lambda name: "/usr/bin/pg_dump", run=lambda *a, **k: Out())


def test_backup_path_is_timestamped_in_utc():
    now = datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
    assert backup_db.backup_path(now).endswith("ratemyhusky_20261010T120000Z.sql.gz")
