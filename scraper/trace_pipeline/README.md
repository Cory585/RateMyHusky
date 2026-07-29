# TRACE Ingest Pipeline (Explorance Blue / Bluera)

Gets a new TRACE term from northeastern.bluera.com into production. The site reads CockroachDB
live: data is public the moment `finalize` finishes precompute. No redeploy involved.

## Per-term runbook

1. **Get the term's report-list id (rid).** Log into https://northeastern.bluera.com → Reports →
   the term's report list ("All reports for students"). The URL contains `rpvlf.aspx?rid=<guid>`.
2. **Refresh cookies.** With that page open: DevTools → Network → click any request to
   `northeastern-bc.bluera.com` → copy the full `Cookie:` request-header value → paste into
   `scraper/trace_pipeline/cookies.txt`. **NEVER commit this file** (it is gitignored; keep it that way).
3. **Scrape** (~25 min for a full semester; resumable — re-run the same command after any failure):
   `python scraper/trace_pipeline/scrape.py --term "Spring 2026" --rid <guid>`
   Output: `scraper/data/raw/Spring 2026.csv` (+ `.urls.json`/`.results.json` state). Check the
   end-of-run summary: errors should be 0 and the term histogram should show exactly one title.
   URL collection is sequential (~20 min for 495 pages — Bluera's pager keeps its position in the
   ASP.NET session, so it can't be parallelized); reports download 6 at a time (`--workers`).
4. **Dry-run the ingest and read the report** (row counts, new-ID allocations, unparseable samples):
   `python scraper/trace_pipeline/ingest.py --term "Spring 2026" --dry-run`
5. **Ingest:** `python scraper/trace_pipeline/ingest.py --term "Spring 2026"`
   Re-doing a term: add `--replace` (deletes that term's rows from DB + CSVs first).
6. Repeat 1–5 for the next term (e.g. "Summer 1 2026").
7. **Publish:** `python scraper/trace_pipeline/finalize.py`
   Runs backup → precompute (**data goes live here**; run at a quiet hour — catalog tables are
   rebuilt) → evidence build → embedding backfill (evidence+embed feed the Ask feature; profile
   pages only need precompute).
8. **Verify:** new term in `SELECT DISTINCT term_id, term_title FROM trace_courses ORDER BY 1 DESC`;
   a professor who taught that term shows its comments; a course average moved; Ask cites a
   new-term comment. `/full` responses are publicly cached 1h — brief staleness self-heals.
9. **Commit:** nothing data-related is tracked; commit only code/doc changes if any.

## When something goes wrong

- **Scrape died mid-run** (cookies expired: "Sign in" errors): refresh cookies.txt, re-run the same
  command — it resumes from `<Term>.results.json`.
- **Scrape stopped with "rate-limit/WAF block"** (403/429/503 from FortiWeb in front of Bluera): it
  aborts on the first one rather than logging thousands of error rows. Wait a few minutes and re-run;
  add `--workers 3` if it recurs.
- **Ingest was wrong/partial:** `python scraper/trace_pipeline/ingest.py --term "<T>" --replace`.
- **Prod tables damaged:** restore the pre-finalize backup:
  `python backend/restore_db.py --tables trace_courses,trace_scores,trace_comments`
  (defaults to the newest dump in backend/backups/; requires typing the cluster name; the site
  errors on those tables while it runs). `--list` shows a dump's contents first.
- **finalize step failed:** fix the cause, resume with `--from <step>` (backup|precompute|evidence|embed).

## Known gotchas (hard-won)

- If a scrape is killed *during URL collection*, delete `<Term>.urls.json` before re-running: an
  existing urls file is treated as complete, so a partial one silently scrapes only part of the term.
- This machine's DNS intermittently fails on *.cockroachlabs.cloud — every tool here retries; if a
  command dies with "could not translate host name" anyway, just re-run.
- CockroachDB serverless: writes are multi-row INSERTs, one statement per commit (5000 rows for
  courses/scores, 1000 for comments — tune via --scores-batch/--comments-batch only if measurements
  say so). Throttling makes runs slower, not broken; the monthly RU budget is the thing to watch
  (Cloud console) — it killed the old cluster.
- Long DB *reads* die after ~75 min (GC TTL) — embed_evidence already recycles its snapshot.
- Legal: TRACE comments are the project's riskiest data source; ingesting a new term re-incurs that
  exposure (see the takedown-risk notes). Honor takedown requests promptly.
- New-term instructors have no photos until a photo re-scrape; they render with default focal crop.
