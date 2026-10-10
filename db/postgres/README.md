# PostgreSQL schema

The production database is PostgreSQL 16 with the pgvector extension, on an
Oracle Cloud VM (US East – Ashburn). These files are the schema's single source
of truth: the application and the regular loaders never create or alter tables.
Two exceptions remain: `scraper/load_catalog.py` keeps `CREATE TABLE IF NOT EXISTS`
DDL for its SQLite test (a no-op against the live database), and
`backend/moderate_backfill.py` adds the `mod_*` columns when run, so running it
against production changes the schema; update `02_tables.sql` if you do.

| File | Run as | When |
|---|---|---|
| `00_extensions.sql` | `postgres` superuser | first (needs `postgresql-16-pgvector`) |
| `02_tables.sql` | `appuser` | before loading data — columns and primary keys only |
| `03_constraints.sql` | `appuser` | after loading data — indexes, unique indexes, CHECKs, the FK |
| `reset_sequences.sql` | `appuser` | after loading data — moves every identity counter past `max(id)` |

Fresh server, in order:

```bash
sudo -u postgres psql -d appdb -v ON_ERROR_STOP=1 -f 00_extensions.sql
psql "$PG_URL" -v ON_ERROR_STOP=1 -f 02_tables.sql
python backend/restore_db.py <dump.sql.gz> --all --yes   # or another data load
psql "$PG_URL" -v ON_ERROR_STOP=1 -f 03_constraints.sql
psql "$PG_URL" -f reset_sequences.sql
```

`backend/tests/pg/` applies 00, 02 and 03 to a scratch database on every run, so
a change here is exercised by the integration tests.
