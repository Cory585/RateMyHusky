# PostgreSQL schema

The production database is PostgreSQL 16 with the pgvector extension, on an
Oracle Cloud VM (US East – Ashburn). These files are the schema's single source
of truth: the application and the loaders never create or alter tables on
PostgreSQL. `scraper/load_banner.py`, `scraper/match_banner_instructors.py` and
`scraper/load_catalog.py` check that the tables and columns they use exist
(`scraper/schema_check.py`) and stop with a list of what is missing; they only
create tables on the SQLite stage files and in tests. The one exception is
`backend/moderate_backfill.py`, which adds the `mod_*` columns when run, so
running it against production changes the schema; update `02_tables.sql` if you do.

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

## Schema changes since the migration

The NUBanner teaching-history code added a table and five columns after the
production database was loaded. `02_tables.sql` already declares them (the
columns last in their tables, where `ADD COLUMN` puts them). A database loaded
before this needs the following, which the loaders no longer run for you. Run it
once at cutover:

```sql
CREATE TABLE IF NOT EXISTS public.banner_roster_duplicates (
    term_code TEXT NOT NULL,
    instructor_key TEXT NOT NULL,
    CONSTRAINT banner_roster_duplicates_pkey PRIMARY KEY (term_code, instructor_key)
);
ALTER TABLE public.banner_sections ADD COLUMN IF NOT EXISTS part_of_term TEXT;
ALTER TABLE public.banner_course_offerings
    ADD COLUMN IF NOT EXISTS summer1_years BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS summer2_years BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS first_term_label TEXT,
    ADD COLUMN IF NOT EXISTS offering_years TEXT;
```

Until they run, the course-history query in `backend/teaching_history.py` fails
on the missing columns, and the Banner loader and matcher stop at start-up naming
the missing table or column.

### Optional cleanup (destructive; take a backup first)

`02_tables.sql` no longer declares the old backup tables or the tables nothing
reads. Production does not need these drops to be compatible with the code; they
only remove dead tables, and they cannot be undone. After taking a backup:

```sql
DROP TABLE public.course_catalog_bak_20260803, public.professors_catalog_bak_20260803,
           public.prof_identity, public.prof_rmp_summary, public.prof_rmp_link,
           public.rating_meta;
```
