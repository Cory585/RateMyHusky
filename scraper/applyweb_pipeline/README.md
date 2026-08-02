# ApplyWeb TRACE Scores Fix Pipeline

Fixes the ApplyWeb-era `trace_scores` corruption (Spring 2021 → Summer 1 2025, plus all
Law terms, which still publish via ApplyWeb after the Bluera cutover). Background:
`docs/plans/APPLYWEB_CORRUPTION_REPORT.md` (local-only, not in the repo).

**NEVER commit** `data/` (fixtures + raw XLS cache are NEU-confidential; this is a public
repo) or `cookies.txt`. Both are gitignored — keep it that way.

## Prereqs

- `pip install requests xlrd python-dotenv psycopg2-binary tqdm`
- `backend/.env` with `NEW_CRDB_DATABASE_URL` set
- ApplyWeb session cookie in `scraper/applyweb_pipeline/cookies.txt`: DevTools on
  applyweb.com → any request → copy the full `Cookie:` request header value into the file.

## Run order

1. `python scraper/applyweb_pipeline/scrape_xls.py --dry-run` — review per-term section
   counts (read-only, two SELECTs).
2. `python scraper/applyweb_pipeline/verify.py --pre` — record the corruption baseline
   (optional).
3. `python scraper/applyweb_pipeline/scrape_xls.py` — hours; resumable; on cookie expiry
   refresh `cookies.txt` and re-run (existing cached files are skipped automatically).
4. `python scraper/applyweb_pipeline/ingest_xls.py --dry-run` — review parse stats
   (expect ~19 rows/section).
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

- Cookie expiry mid-scrape costs nothing to resume — refresh `cookies.txt`, re-run the
  same command.
- Review `failures.csv` / `parse_failures.csv` after a run; don't assume a clean exit
  means every section downloaded/parsed.
- Law terms still publish via ApplyWeb — this pipeline is the Law path going forward, not
  a one-off migration.
- Bluera terms (`term_id >= 900`) are never touched; both `replace_sections` and
  `delete_full_term` refuse before issuing any SQL.
- `--delete-full-term` purges the whole term in the DB, but the CSV dual-write only drops
  ingested sections — only use the flag on a term after confirming its dry-run shows 0
  dirty sections and full download coverage.
- On the real scrape run, watch the first minute: if ApplyWeb serves an HTML login page
  with HTTP 200 instead of 401 on cookie expiry, downloads degrade to `fail` (caught by
  the magic-byte check) rather than halting — a ballooning `failures.csv` early means a
  bad cookie.
- `verify.py --pre` runs before the `count_na` migration exists in prod; Gate 4 reporting
  `ERROR`/`SKIP` in `--pre` mode is expected, not a problem.
