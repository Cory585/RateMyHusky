"""One-off cleanup: remove the retired TRACE course-evaluation data from the database.

The site no longer reads or writes TRACE. This drops what is left of it:

  1. trace_courses, trace_scores, trace_comments   DROP TABLE IF EXISTS ... CASCADE
                                                    (their indexes and unique
                                                    constraints go with them)
  2. evidence rows with source = 'trace', and the evidence_embeddings rows that
     reference them. Deleted in batches: the TRACE comment corpus is large enough
     that one DELETE would exceed a CockroachDB transaction.

Dry-run by default; it reports counts and changes nothing. Pass --yes to execute.

    python drop_trace_data.py           # dry run
    python drop_trace_data.py --yes     # apply

Reads CRDB_DATABASE_URL like every other script here (see migrate_to_crdb.get_connection).
This is the one file that still names the TRACE tables, because deleting them is its job.
"""

import argparse
import sys

TRACE_TABLES = ("trace_comments", "trace_scores", "trace_courses")
BATCH = 5000


def _table_exists(cur, table):
    cur.execute("SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s", (table,))
    return cur.fetchone()[0] > 0


def _count(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def drop_trace_data(conn, execute=False):
    """Drop the TRACE tables and evidence rows. Returns a dict of counts."""
    cur = conn.cursor()
    report = {}

    for table in TRACE_TABLES:
        if not _table_exists(cur, table):
            report[table] = None
            print(f"  {table:22s} absent")
            continue
        report[table] = _count(cur, f"SELECT count(*) FROM {table}")
        print(f"  {table:22s} {report[table]:>10,} rows"
              + ("" if execute else " (dry run)"))

    have_evidence = _table_exists(cur, "evidence")
    have_embeddings = _table_exists(cur, "evidence_embeddings")
    ev = emb = 0
    if have_evidence:
        ev = _count(cur, "SELECT count(*) FROM evidence WHERE source = 'trace'")
        if have_embeddings:
            emb = _count(cur, "SELECT count(*) FROM evidence_embeddings WHERE evidence_id IN "
                              "(SELECT id FROM evidence WHERE source = 'trace')")
    report["evidence"] = ev
    report["evidence_embeddings"] = emb
    print(f"  {'evidence_embeddings':22s} {emb:>10,} rows" + ("" if execute else " (dry run)"))
    print(f"  {'evidence':22s} {ev:>10,} rows" + ("" if execute else " (dry run)"))

    if not execute:
        print("\nDry run — nothing was changed. Re-run with --yes to apply.")
        return report

    # Embeddings before evidence, per batch, so no vector is ever left joined to
    # a deleted row even if the run is interrupted between batches.
    while have_evidence and ev:
        cur.execute("SELECT id FROM evidence WHERE source = 'trace' LIMIT %s", (BATCH,))
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            break
        if have_embeddings:
            cur.execute("DELETE FROM evidence_embeddings WHERE evidence_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM evidence WHERE id = ANY(%s)", (ids,))
        conn.commit()
        print(f"  deleted {len(ids):,} evidence rows...", end="\r")

    for table in TRACE_TABLES:
        if report[table] is not None:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            conn.commit()
            print(f"  dropped {table}")

    print("\nDone.")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--yes", action="store_true",
                        help="Execute. Without it this is a dry run.")
    args = parser.parse_args(argv)

    from migrate_to_crdb import get_connection
    conn = get_connection()
    try:
        drop_trace_data(conn, execute=args.yes)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
