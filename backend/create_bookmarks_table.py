"""
Create the bookmarks table in CockroachDB.
Idempotent — safe to re-run (CREATE TABLE IF NOT EXISTS).
Reads NEW_CRDB_DATABASE_URL (fallback CRDB_DATABASE_URL) from backend/.env —
the local .env's CRDB_DATABASE_URL points at the old, disabled cluster.

Run:  python backend/create_bookmarks_table.py
"""

import os
import sys
import time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DATABASE_URL = os.getenv("NEW_CRDB_DATABASE_URL") or os.getenv("CRDB_DATABASE_URL")

if not DATABASE_URL:
    sys.exit("Missing NEW_CRDB_DATABASE_URL (or CRDB_DATABASE_URL) in backend/.env")

import psycopg2

DDL = """
    CREATE TABLE IF NOT EXISTS bookmarks (
        id INT8 DEFAULT unique_rowid() PRIMARY KEY,
        user_sub TEXT NOT NULL,
        item_type TEXT NOT NULL CHECK (item_type IN ('professor', 'course')),
        item_key TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (user_sub, item_type, item_key)
    );
    CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks (user_sub, created_at DESC);
"""


def connect_database(attempts=5):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg2.connect(DATABASE_URL, sslmode="require")
        except psycopg2.OperationalError as exc:
            message = str(exc)
            if "could not translate host name" not in message:
                raise
            last_error = message
            if attempt < attempts:
                print(f"  DNS lookup failed; retrying ({attempt}/{attempts})...")
                time.sleep(2)
    sys.exit(
        f"Could not resolve the hostname after {attempts} attempts.\n"
        f"Original error:\n{last_error}"
    )


def main():
    print("Connecting to CockroachDB (CRDB_DATABASE_URL)...")
    conn = connect_database()
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()
    conn.close()
    print("bookmarks table ready.")


if __name__ == "__main__":
    main()
