# RateMyHusky

**https://ratemyhusky.com/**

RateMyHusky is a full-stack web app for Northeastern University students to discover, search, and compare professors. It combines RateMyProfessors (RMP) crowdsourced reviews with Northeastern's official TRACE course evaluations into a single unified platform.

### Data Sources

The app aggregates two distinct sources of professor and course data:

- **RateMyProfessors (RMP)** — Crowdsourced student reviews with quality and difficulty ratings, "would take again" percentages, grade distributions, and written comments. This captures student sentiment broadly but varies in review volume and can be unrepresentative for less popular professors.

- **TRACE (Teaching and Research Assessment of the College Experience)** — Northeastern's official end-of-semester course evaluation system. TRACE scores cover structured dimensions like teaching effectiveness, organization, rigor, grading fairness, and accessibility. These are systematically collected per course section, making them more uniform — but they were historically harder to access in aggregate.

By combining both, RateMyHusky surfaces a blended overall rating alongside the raw scores from each source, so students can see consensus ratings without losing the nuance of where those ratings come from.

---

## Features

### Professor Profiles
Each professor has a dedicated page showing:
- A blended overall rating (combining RMP quality and TRACE scores), difficulty, "would take again" percentage, and average hours per week
- A full rating distribution chart showing the spread of individual scores
- Grade distribution pulled from RMP reviews
- A TRACE radar chart comparing the professor to their department average across five evaluation dimensions: Teaching, Organization, Rigor, Grading, and Accessibility
- Filterable reviews — switch between RMP student comments and TRACE written feedback, or filter by specific course
- A "Courses Taught" section listing every course the professor has taught with term tags and links to individual course pages

### Course Pages
Course detail pages show all known sections for a course, the instructors who have taught each section, and per-section ratings so students can see how the same course varies across professors and semesters.

### Professor Catalog
A searchable, filterable listing of all professors in the database. Students can filter by college, department, rating range, and minimum review volume. Results are paginated and sortable.

### Course Catalog
A browsable listing of all courses, filterable by department, with ratings and links to full course detail pages.

### Side-by-Side Comparison
The comparison view lets students place two professors next to each other and directly compare their ratings, TRACE scores, and review counts — useful when choosing between sections of the same course.

### Homepage Leaderboard
The homepage features a "GOATED Professors" leaderboard ranked by college. An animated pill selector lets users switch between colleges to see the top-rated professors in each. This doubles as a discovery tool for students who don't know who to search for.

### Search with Autocomplete
A unified search bar across the entire site handles both professor names and course names with typeahead suggestions, making it fast to navigate directly to any profile.

### Shuffle / "Feeling Lucky"
A randomizer wheel on the homepage spins to a random professor — intended for discovery and for students curious to explore outside their department.

### Google OAuth Sign-In
Some content (such as full TRACE written comments) is gated behind Google OAuth sign-in using Northeastern email accounts. This keeps detailed evaluation data accessible to the Northeastern community while limiting broad public access.

### Dark Mode
A site-wide dark/light mode toggle with persistence across sessions.

---

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 19, TypeScript, Vite, React Router |
| Charts | Recharts (radar charts, rating distributions) |
| Backend | Python, Flask, Flask-CORS, Flask-Limiter (per-IP rate limiting) |
| Auth | Google OAuth 2.0, JWT (PyJWT) |
| Database | CockroachDB (via psycopg2, SSL required) |
| Caching | In-memory TTL cache with daily reset (server-side) |
| Data ingestion | CSV-based scraper outputs + migration scripts |
| Hosting | Frontend on Vercel, backend on Heroku |

---

## Prerequisites

- Python 3.8+
- Node.js 18+
- npm
- A reachable CockroachDB instance

---

## Quick Start

1. Unzip `trace_comments.zip` into `backend/Better_Scraper/output_data/`.
2. Install backend dependencies.
3. Configure backend environment variables.
4. Start backend API server.
5. Install frontend dependencies.
6. Start frontend dev server.

Detailed commands are below.

---

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

---

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Frontend default: http://localhost:5173

The frontend calls the backend API on port 5001 in local development.

---

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

---

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
│       ├── api/
│       ├── components/
│       ├── context/
│       └── pages/
└── README.md
```

---

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
