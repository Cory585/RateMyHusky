# RateMyHusky

RateMyHusky is a full-stack web app for discovering, searching, and comparing Northeastern University professors.

It combines:
- RateMyProfessors (RMP) data — crowdsourced quality/difficulty ratings and student reviews
- TRACE course evaluation data — Northeastern's official end-of-semester survey scores per course section
- Internal profile metadata such as course history and professor photos

## Features

- Professor catalog with filters for college, department, ratings, and review volume
- Professor profile pages with RMP ratings, TRACE scores, comments, and related courses
- Side-by-side professor comparison view
- Search with autocomplete for professors and courses
- Shuffle/random discovery experience
- Google OAuth sign-in flow for gated functionality
- Responsive UI with theme support

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 19, TypeScript, Vite, React Router |
| Backend | Python, Flask, Flask-CORS, Flask-Limiter (per-IP rate limiting) |
| Auth | Google OAuth 2.0, JWT (PyJWT) |
| Database | CockroachDB (via psycopg2, SSL required) |
| Data ingestion | CSV-based scraper outputs + migration scripts |
| Scrapers | Custom Python scripts for RMP, TRACE PDFs, and professor photos |

## Prerequisites

- Python 3.8+
- Node.js 18+
- npm
- A reachable CockroachDB instance

## Quick Start

1. Unzip `trace_comments.zip` into `backend/Better_Scraper/output_data/`.
2. Install backend dependencies.
3. Configure backend environment variables.
4. Start backend API server.
5. Install frontend dependencies.
6. Start frontend dev server.

Detailed commands are below.

## Backend Setup

From the repository root:

```bash
pip install -r backend/requirements.txt
```

Create `backend/.env` with at least:

```env
CRDB_DATABASE_URL=<your-cockroachdb-connection-string>
JWT_SECRET=<generate-with-openssl-rand-hex-32>
```

Generate a secure JWT secret with:

```bash
openssl rand -hex 32
```

Optional (required for Google OAuth login flow):

```env
GOOGLE_CLIENT_ID=<your-google-oauth-client-id>
GOOGLE_CLIENT_SECRET=<your-google-oauth-client-secret>
FRONTEND_URL=http://localhost:5173
```

Run the backend:

```bash
python backend/server.py
```

Backend default: http://localhost:5001

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Frontend default: http://localhost:5173

The frontend calls the backend API on port 5001 in local development.

## Data Setup

**Required:** Unzip `trace_comments.zip` before running any scraper or migration workflows:

```bash
unzip trace_comments.zip -d backend/Better_Scraper/output_data/
```

Additional notes:
- Scraper files are in `backend/Better_Scraper/` and `scraper/`.
- CSV outputs land in `backend/Better_Scraper/output_data/`.
- The backend runtime serves data from CockroachDB, so CSV files are only needed for ingestion/migration workflows.

### Ingestion pipeline (one-time setup)

After unzipping the data, load it into CockroachDB with the migration script:

```bash
python backend/migrate_to_crdb.py all
```

This is idempotent — safe to re-run. It pre-filters rows client-side to avoid re-inserting data already in the database.

Then run the precomputation step to build derived/aggregated tables:

```bash
python backend/precompute.py
```

This runs locally (requires `pandas`/`numpy`) so those heavy dependencies are not needed on the deployed server.

## Project Structure

```text
.
├── backend/
│   ├── server.py              # Flask API server (entry point)
│   ├── requirements.txt
│   ├── migrate_to_crdb.py     # Load CSV data into CockroachDB (idempotent)
│   ├── precompute.py          # Build derived/aggregated tables locally
│   └── Better_Scraper/
│       ├── fetch.py           # RMP scraper
│       ├── trace_scrape.py    # TRACE PDF scraper
│       ├── photo_scrape.py    # Professor photo scraper
│       └── output_data/       # CSV outputs (and trace_comments.zip)
├── scraper/
│   ├── main.py                # Orchestrates scraper runs
│   └── regen_csv.py           # Rebuilds CSVs from raw data
├── frontend/
│   ├── package.json
│   └── src/
│       ├── api/               # Typed fetch wrappers for the Flask API
│       ├── components/        # Shared UI components
│       ├── context/           # React context (auth, theme, etc.)
│       └── pages/             # Route-level page components
└── README.md
```

## Useful Commands

Backend:

```bash
python backend/server.py
```

Frontend:

```bash
cd frontend
npm run dev
npm run build
npm run preview
```
