"""
Restore tables from a backup_db.py dump into the existing PostgreSQL schema.

Usage:
    python backend/restore_db.py <dump.sql.gz> --tables rmp_professors,rmp_reviews [--yes]
    python backend/restore_db.py <dump.sql.gz> --all [--yes]
    python backend/restore_db.py <dump.sql.gz> --list
    python backend/restore_db.py --selftest

The dump is data only; the schema comes from db/postgres/. For the chosen tables
this DELETEs their rows, reloads them and resets their identity counters, all
in ONE transaction (psql --single-transaction): a DELETE takes only row locks, so
the live site keeps reading the old rows until the restore commits, and any
failure rolls everything back. (TRUNCATE would hold an ACCESS EXCLUSIVE lock and
block every query on those tables until COMMIT.)
Choosing evidence also restores evidence_embeddings, which references it.

Only dumps written by pg_dump (backup_db.py) are accepted; older backups used a
different format and cannot be restored with this tool.

Needs psql on PATH, at least as new as the pg_dump that wrote the dump.
Reads CRDB_DATABASE_URL from backend/.env.
"""

import argparse
import glob
import gzip
import os
import re
import subprocess
import sys
from urllib.parse import quote, unquote, urlparse

HEADER_RE = re.compile(
    r"^-- (?:Data for )?Name: (?P<name>[^;]+); Type: (?P<type>TABLE DATA|SEQUENCE SET);")
TRAILER = "-- PostgreSQL database dump complete"
PG_DUMP_MARK = "-- PostgreSQL database dump"
# Restoring a table without the tables that reference it would fail its DELETE.
DEPENDENTS = {"evidence": {"evidence_embeddings"}}

_FIXTURE_SQL = """--
-- PostgreSQL database dump
--

\\restrict abc123

-- Dumped from database version 16.10
-- Dumped by pg_dump version 16.10

SET statement_timeout = 0;
SELECT pg_catalog.set_config('search_path', '', false);

--
-- Data for Name: rmp_reviews; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.rmp_reviews (id, professor_name, comment) VALUES
\t(1, 'Ada Lovelace', 'simple'),
\t(2, 'Ada Lovelace', 'tricky; has ''quoted'' text;
and a newline');


--
-- Data for Name: stats_cache; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.stats_cache (key, value) VALUES
\t('professors', 9385);


--
-- Name: rmp_reviews_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.rmp_reviews_id_seq', 2, true);


--
-- PostgreSQL database dump complete
--

\\unrestrict abc123
"""


def section_table(kind, name):
    """The table a section restores: its own name for TABLE DATA, the owning
    table for an identity sequence (<table>_<column>_seq)."""
    name = name.strip().strip('"')
    if kind == "SEQUENCE SET":
        parts = name.rsplit("_", 2)
        if len(parts) == 3 and parts[2] == "seq":
            return parts[0]
    return name


def dump_tables(lines):
    """Tables with a data section in the dump, in dump order."""
    seen = []
    for line in lines:
        m = HEADER_RE.match(line)
        if m and m.group("type") == "TABLE DATA":
            table = section_table("TABLE DATA", m.group("name"))
            if table not in seen:
                seen.append(table)
    return seen


def expand_tables(tables):
    out = set(tables)
    for table in tables:
        out |= DEPENDENTS.get(table, set())
    return out


def delete_sql(tables):
    """Per-table DELETEs (dependents before the tables they reference) behind a
    5s lock_timeout, so the restore fails fast instead of queueing behind readers."""
    dependents = set().union(*(DEPENDENTS.get(t, set()) for t in tables)) & set(tables)
    ordered = sorted(dependents) + sorted(set(tables) - dependents)
    return "SET lock_timeout = '5s';\n" + "".join(f'DELETE FROM public."{t}";\n' for t in ordered)


def added_note(requested, expanded):
    """One line naming tables expand_tables added and why, or None."""
    added = sorted(set(expanded) - set(requested))
    if not added:
        return None
    parts = []
    for table in added:
        owners = sorted(o for o, deps in DEPENDENTS.items() if table in deps and o in requested)
        parts.append(f"{table} references {', '.join(owners)}")
    return "; ".join(parts) + ", so it is restored too."


def client_connection(url):
    """(dsn without the password, env) for a psql/pg_dump subprocess: the password
    travels in PGPASSWORD, not on the command line, and SSL is required."""
    u = urlparse(url)
    env = {**os.environ, "PGSSLMODE": "require"}
    netloc = u.netloc
    if u.password is not None:
        env["PGPASSWORD"] = unquote(u.password)
        host = netloc.rsplit("@", 1)[1]
        netloc = (quote(unquote(u.username), safe="") + "@" if u.username else "") + host
    return u._replace(netloc=netloc).geturl(), env


def filter_dump(lines, tables):
    """Yield the dump restricted to `tables`, with their DELETEs injected
    once, just before the first section. Streams line by line.

    The preamble (session SETs, pg_dump's \\restrict) and the trailer pass through;
    each section passes iff its table is selected. The DELETEs are schema-qualified
    because the preamble empties search_path."""
    keep = True
    injected = False
    for line in lines:
        m = HEADER_RE.match(line)
        if m:
            if not injected:
                yield delete_sql(tables)
                injected = True
            keep = section_table(m.group("type"), m.group("name")) in tables
        elif line.startswith(TRAILER):
            keep = True
        if keep:
            yield line


def check_format(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        head = [f.readline() for _ in range(10)]
    if not any(line.startswith(PG_DUMP_MARK) for line in head):
        raise ValueError(f"{os.path.basename(path)} is not a pg_dump dump "
                         "(older backups cannot be restored with this tool)")


def restore(url, path, tables, psql="psql"):
    """Stream the filtered dump into one psql transaction.

    If reading the dump fails partway, psql is killed BEFORE its stdin closes:
    closing stdin would make psql COMMIT the DELETEs plus a partial reload.

    Bytes in, bytes out: no newline translation, so a carriage return inside a quoted value
    reaches psql exactly as pg_dump wrote it."""
    dsn, env = client_connection(url)
    proc = subprocess.Popen(
        [psql, f"--dbname={dsn}", "--single-transaction", "--quiet",
         "-v", "ON_ERROR_STOP=1", "-f", "-"],
        stdin=subprocess.PIPE, env=env)
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            for line in filter_dump(f, tables):
                proc.stdin.write(line.encode("utf-8"))
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"psql exited with status {proc.returncode}; the restore was rolled back.")


def confirm(host, tables, dump_name, input_fn=input, assume_yes=False):
    label = host if re.fullmatch(r"[\d.:]+", host or "") else host.split(".")[0]
    print(f"\n*** DESTRUCTIVE RESTORE ***\n  Target host: {host}\n  Dump: {dump_name}"
          f"\n  Tables to DELETE + reload: {', '.join(sorted(tables))}"
          "\n  Runs as one transaction; the site keeps reading the old rows until it commits.")
    if assume_yes:
        return True
    return input_fn(f"Type '{label}' to proceed: ").strip() == label


def _newest_dump(backup_dir=None):
    backup_dir = backup_dir or os.path.join(os.path.dirname(__file__), "backups")
    candidates = glob.glob(os.path.join(backup_dir, "ratemyhusky_*.sql.gz"))
    return max(candidates, key=os.path.getmtime) if candidates else None


def selftest():
    fails = []

    def check(label, cond):
        if not cond:
            fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    lines = _FIXTURE_SQL.splitlines(keepends=True)
    check("tables listed in dump order", dump_tables(lines) == ["rmp_reviews", "stats_cache"])
    out = "".join(filter_dump(lines, {"rmp_reviews"}))
    check("one DELETE, before the data", out.count("DELETE FROM") == 1 and "TRUNCATE" not in out
          and out.index("DELETE FROM") < out.index("INSERT INTO public.rmp_reviews"))
    check("sequence reset kept with its table", "rmp_reviews_id_seq" in out)
    check("unselected table dropped", "stats_cache" not in out)
    check("preamble and trailer kept", "\\restrict" in out and "\\unrestrict" in out)
    check("evidence brings its embeddings", expand_tables({"evidence"}) == {"evidence", "evidence_embeddings"})
    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


def main():
    parser = argparse.ArgumentParser(description="Restore tables from a backup_db.py dump.")
    parser.add_argument("dump", nargs="?", help="Path to a .sql.gz dump (default: newest in backend/backups/)")
    parser.add_argument("--list", action="store_true", help="List tables in the dump and exit (no DB connection)")
    parser.add_argument("--tables", help="Comma-separated table names to restore")
    parser.add_argument("--all", action="store_true", help="Restore every table in the dump")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    parser.add_argument("--selftest", action="store_true", help="Run the offline selftest and exit")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    dump = args.dump or _newest_dump()
    if not dump:
        sys.exit("No dump given and no backend/backups/ratemyhusky_*.sql.gz found.")
    if not os.path.isfile(dump):
        sys.exit(f"Dump file not found: {dump}")
    try:
        check_format(dump)
    except ValueError as e:
        sys.exit(str(e))

    with gzip.open(dump, "rt", encoding="utf-8", newline="") as f:
        in_dump = dump_tables(f)
    if args.list:
        print("\n".join(in_dump))
        return

    if bool(args.tables) == bool(args.all):
        sys.exit("Specify exactly one of --tables a,b,c or --all.")
    tables = set(in_dump) if args.all else {t.strip() for t in args.tables.split(",") if t.strip()}
    missing = sorted(tables - set(in_dump))
    if missing:
        sys.exit(f"Not in this dump: {', '.join(missing)}")
    requested = tables
    tables = expand_tables(tables)
    note = added_note(requested, tables)
    if note:
        print(note)
    added = sorted(tables - set(in_dump))
    if added:
        sys.exit(f"Restoring these requires {', '.join(added)}, which this dump does not contain.")

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    db_url = os.getenv("CRDB_DATABASE_URL")
    if not db_url:
        sys.exit("Missing CRDB_DATABASE_URL in backend/.env")

    if not confirm(urlparse(db_url).hostname, tables, os.path.basename(dump), assume_yes=args.yes):
        sys.exit("Aborted: confirmation not given.")
    restore(db_url, dump, tables)
    print(f"Restored {len(tables)} table(s): {', '.join(sorted(tables))}")


if __name__ == "__main__":
    main()
