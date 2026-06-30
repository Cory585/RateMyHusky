"""
Back up the CockroachDB cluster to a single gzipped SQL dump, matching the
existing format in backend/backups/ (DROP+CREATE+INSERT per table).
Reads CRDB_DATABASE_URL from .env. Reuses transfer_db's DNS-retry connect logic
(this machine's resolver intermittently fails on the CRDB Cloud hostname).

Run:  python backend/backup_db.py
Output: backend/backups/ratemyhusky_new_<UTC>Z.sql.gz
"""

import os, sys, gzip, io, time, datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
import psycopg2

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DB_URL = os.getenv("CRDB_DATABASE_URL")
if not DB_URL:
    sys.exit("Missing CRDB_DATABASE_URL in backend/.env")

BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")
BATCH_SIZE = 5000


def connect_database(label, url, attempts=8):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg2.connect(url, sslmode="require")
        except psycopg2.OperationalError as exc:
            message = str(exc)
            if "could not translate host name" not in message:
                raise
            last_error = message
            if attempt < attempts:
                print(f"  DNS lookup failed for {label}; retrying ({attempt}/{attempts})...")
                time.sleep(3)
    sys.exit(f"Could not resolve the hostname in {label} after {attempts} attempts.\n"
             f"Original error:\n{last_error}")


def get_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        return [r[0] for r in cur.fetchall()]


def dump_table(conn, out, table):
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{table}"')
        total = cur.fetchone()[0]
        cur.execute(f"SHOW CREATE TABLE {table}")
        create_sql = cur.fetchone()[1]

    out.write("\n-- ============================================\n")
    out.write(f"-- Table: {table}  ({total:,} rows)\n")
    out.write("-- ============================================\n")
    out.write(f"DROP TABLE IF EXISTS {table} CASCADE;\n")
    out.write(create_sql.rstrip().rstrip(";") + ";\n\n")

    if total == 0:
        return total

    with conn.cursor() as col_cur:
        col_cur.execute(f'SELECT * FROM "{table}" LIMIT 0')
        cols = [d[0] for d in col_cur.description]
    col_list = ", ".join(f'"{c}"' for c in cols)
    prefix = f"INSERT INTO {table} ({col_list}) VALUES\n"
    tmpl = "(" + ",".join(["%s"] * len(cols)) + ")"

    written = 0
    with conn.cursor(name=f"read_{table}") as read_cur:
        read_cur.itersize = BATCH_SIZE
        read_cur.execute(f'SELECT {col_list} FROM "{table}"')
        with conn.cursor() as mog:  # plain cursor for mogrify
            batch = []
            def flush():
                if not batch:
                    return
                out.write(prefix)
                rows = [mog.mogrify(tmpl, r).decode("utf-8") for r in batch]
                out.write(",\n".join(rows) + ";\n\n")
            for row in read_cur:
                batch.append(row)
                if len(batch) >= BATCH_SIZE:
                    flush(); written += len(batch)
                    print(f"  {table}: {written:,} / {total:,}...", end="\r")
                    batch = []
            if batch:
                flush(); written += len(batch)
    print(f"  {table}: {written:,} / {total:,} rows written")
    return written


def main():
    print("Connecting to CockroachDB (CRDB_DATABASE_URL)...")
    conn = connect_database("CRDB_DATABASE_URL", DB_URL)

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    host = urlparse(DB_URL).hostname
    path = os.path.join(BACKUP_DIR, f"ratemyhusky_new_{stamp}.sql.gz")

    tables = get_tables(conn)
    print(f"Found {len(tables)} tables. Writing {os.path.basename(path)}\n")

    # Owner-only (0o600) from creation — the dump is a full copy of the prod DB.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as gz, \
            io.TextIOWrapper(gz, encoding="utf-8") as out:
        out.write("-- ratemyhusky NEW cluster backup\n")
        out.write(f"-- generated (UTC): {stamp}\n")
        out.write(f"-- source host: {host}\n")
        out.write(f"-- tables: {', '.join(tables)}\n")
        out.write("SET sql_safe_updates = false;\n\n")
        for t in tables:
            dump_table(conn, out, t)

    conn.close()
    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"\nBackup complete: {path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
