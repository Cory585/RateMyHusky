# ApplyWeb TRACE Scores Fix Pipeline

Fixes the ApplyWeb-era `trace_scores` corruption (Spring 2021 → Summer 1 2025, plus all
Law terms, which still publish via ApplyWeb after the Bluera cutover). Background:
`docs/plans/APPLYWEB_CORRUPTION_REPORT.md` (local-only, not in the repo).

**NEVER commit** `data/` (fixtures + raw XLS cache + browser profile are NEU-confidential;
this is a public repo). It is gitignored — keep it that way.

## Prereqs

- `pip install playwright xlrd python-dotenv psycopg2-binary tqdm`
- `backend/.env` with `NEW_CRDB_DATABASE_URL` set
- Google Chrome installed — the scraper drives your real Chrome (headed) via Playwright;
  no `playwright install` download needed.

## Run order

1. `python scraper/applyweb_pipeline/scrape_xls.py --dry-run` — review per-term section
   counts (read-only, two SELECTs).
2. `python scraper/applyweb_pipeline/verify.py --pre` — record the corruption baseline
   (optional).
3. `python scraper/applyweb_pipeline/scrape_xls.py` — hours; resumable. A Chrome window
   opens; on first run log in (NEU SSO + Duo) when prompted in the console. The session
   persists in `data/browser_profile/`, so later runs usually skip login. If the session
   expires mid-run the scrape pauses — re-login in the window and it resumes automatically.
4. `python scraper/applyweb_pipeline/ingest_xls.py --dry-run` — review parse stats
   (expect ~20 rows/section: 19 Likert + hours-per-week; a term stuck at ~19.0 means
   its XLS files lack the All Responses sheet, i.e. no hours data).
5. `python scraper/applyweb_pipeline/ingest_xls.py` — migrates the schema (`count_na`),
   section-scoped DELETE+INSERT per term, rewrites
   `backend/Better_Scraper/output_data/trace_scores.csv` (with a `.bak`).
6. `python scraper/applyweb_pipeline/verify.py` — all gates must PASS.
7. `python backend/backup_db.py` then `python backend/precompute.py` — ratings go live
   (no redeploy). Evidence/embedding steps are NOT needed (scores don't feed RAG).

## Selftests

```bash
python scraper/applyweb_pipeline/common.py --selftest
python scraper/applyweb_pipeline/parse_xls.py --selftest --require-fixtures
python scraper/applyweb_pipeline/scrape_xls.py --selftest
python scraper/applyweb_pipeline/ingest_xls.py --selftest
python scraper/applyweb_pipeline/verify.py --selftest
```
All five print `ALL PASS`.

## Gotchas

- Session expiry mid-run pauses the scrape and prints a re-login prompt — log back in via
  the open Chrome window; nothing is lost.
- If the run pauses for login but you ARE logged in, all of the probe's candidate sections
  (it tries up to 3) would have to be broken — vanishingly unlikely; if it ever happens,
  use `--terms`/`--limit` to start from a different slice. After the first successful
  download the probe uses a known-good section.
- Review `failures.csv` / `parse_failures.csv` after a run; don't assume a clean exit
  means every section downloaded/parsed.
- Law terms still publish via ApplyWeb — this pipeline is the Law path going forward, not
  a one-off migration.
- Bluera terms (`term_id >= 900`) are never touched; both `replace_sections` and
  `delete_full_term` refuse before issuing any SQL.
- `--delete-full-term` purges the whole term in the DB, but the CSV dual-write only drops
  ingested sections — only use the flag on a term after confirming its dry-run shows 0
  dirty sections and full download coverage.
- `verify.py --pre` runs before the `count_na` migration exists in prod; Gate 4 reporting
  `ERROR`/`SKIP` in `--pre` mode is expected, not a problem.
- If `ingest_xls.py` aborts mid-run, the DB is partially updated while the CSV rewrite
  (which happens after all terms) hasn't run — re-run ingest to completion before
  `backup_db.py`/`precompute.py`; the run is idempotent (section-scoped delete+insert).
