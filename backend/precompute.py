"""
Precomputation script. Builds derived tables in CockroachDB from the RMP CSVs.
This runs on your local machine (needs pandas/numpy) so the deployed server doesn't.

Usage: python precompute.py
"""

import os, re, time, unicodedata
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

from denylist import denied_hashes, is_denied_key

load_dotenv()

CRDB_URL = os.getenv("NEW_CRDB_DATABASE_URL") or os.getenv("CRDB_DATABASE_URL")
if not CRDB_URL:
    raise RuntimeError("NEW_CRDB_DATABASE_URL required in .env")


def _connect(attempts=20):
    """The local resolver flakes on *.cockroachlabs.cloud; retry on DNS failure."""
    last = None
    for i in range(1, attempts + 1):
        try:
            return psycopg2.connect(CRDB_URL, sslmode="require")
        except psycopg2.OperationalError as e:
            if "could not translate host name" not in str(e):
                raise
            last = str(e)
            print(f"  DNS lookup flaked; retrying ({i}/{attempts})...")
            time.sleep(3)
    raise RuntimeError(f"Could not resolve CRDB host after {attempts} attempts.\n{last}")


def normalize_name(name):
    s = str(name).strip().lower()
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def name_to_slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def upgrade_image_url(url):
    return re.sub(r'-\d+x\d+(?=\.\w+$)', '', str(url))


from prof_aliases import ALIAS_MAP

COLLEGE_MAP = {
    "Computer Science": "Khoury", "Information Science": "Khoury",
    "Information Systems": "Khoury", "Computer & Informational Tech.": "Khoury",
    "Computer amp Informational Tech.": "Khoury", "Computer  Informational Tech.": "Khoury",
    "Computer Engineering": "Khoury", "Cybersecurity": "Khoury",
    "Data Science": "Khoury", "Computer Information Systm": "Khoury",
    "Grad Engineering - Multidiscpl": "Engineering",
    "Engineering": "Engineering", "Electrical Engineering": "Engineering",
    "Mechanical Engineering": "Engineering", "Civil Engineering": "Engineering",
    "Chemical Engineering": "Engineering", "Industrial Engineering": "Engineering",
    "Materials Engineering": "Engineering", "Engineering Technology": "Engineering",
    "Electronics": "Engineering", "Electrical & Computer Engr": "Engineering",
    "Mechanical & Industrial Eng": "Engineering", "Civil & Environmental Eng": "Engineering",
    "Bioengineering": "Engineering", "Industrial Technology": "Engineering",
    "Business": "Business", "Business Administration": "Business",
    "Finance": "Business", "Finance & Insurance": "Business",
    "Accounting": "Business", "Accounting & Finance": "Business",
    "Marketing": "Business", "Management": "Business",
    "Entrepreneurship": "Business", "International Business": "Business",
    "Supply Chain Management": "Business", "Operations Management": "Business",
    "Managerial Science": "Business", "Organizational Behavior": "Business",
    "Organizational Leadership": "Business", "Human Resources Management": "Business",
    "Leadership": "Business",
    "Dean of College of Sciences": "Science",
    "Mathematics": "Science", "Physics": "Science", "Chemistry": "Science",
    "Biology": "Science", "Biochemistry": "Science",
    "Environmental Science": "Science", "Environmental Studies": "Science",
    "Marine Sciences": "Science", "Marine Biology": "Science",
    "Microbiology": "Science", "Biotechnology": "Science",
    "Geology": "Science", "Earth Science": "Science",
    "Biomedical": "Science", "Science": "Science", "Math": "Science",
    "Behavioral Neuroscience": "Science",
    "Art": "CAMD", "Art History": "CAMD", "Architecture": "CAMD",
    "Communication Studies": "CAMD", "Communication": "CAMD",
    "Communications": "CAMD", "Journalism": "CAMD",
    "Media": "CAMD", "Media Studies": "CAMD",
    "Graphic Design": "CAMD", "Design": "CAMD",
    "Music": "CAMD", "Music Technology": "CAMD", "Music Business": "CAMD",
    "Theater": "CAMD", "Game Design": "CAMD", "Fine Arts": "CAMD",
    "Visual Arts": "CAMD", "Cinema": "CAMD", "Photography": "CAMD",
    "Multimedia": "CAMD", "Creative Studies": "CAMD",
    "Health Science": "Health Sciences", "Health Sciences": "Health Sciences",
    "Nursing": "Health Sciences", "Pharmacy": "Health Sciences",
    "Physical Therapy": "Health Sciences",
    "Speech & Hearing Sciences": "Health Sciences",
    "Speech Language Pathology": "Health Sciences",
    "Health Management": "Health Sciences",
    "Health  Physical Education": "Health Sciences",
    "Medicine": "Health Sciences", "Regulatory Affairs": "Health Sciences",
    "Counseling Psychology": "Health Sciences", "Applied Psychology": "Health Sciences",
    "Political Science": "CSSH", "Economics": "CSSH", "History": "CSSH",
    "Psychology": "CSSH", "Sociology": "CSSH", "Philosophy": "CSSH",
    "English": "CSSH", "Writing": "CSSH", "Literature": "CSSH",
    "Linguistics": "CSSH", "Languages": "CSSH", "Modern Languages": "CSSH",
    "Spanish": "CSSH", "French": "CSSH", "Arabic": "CSSH",
    "Sign Language": "CSSH", "World Languages Center": "CSSH",
    "Criminal Justice": "CSSH", "Anthropology": "CSSH",
    "Human Services": "CSSH", "Religious Studies": "CSSH",
    "Judaic Studies": "CSSH", "International Studies": "CSSH",
    "International Affairs": "CSSH", "International Politics": "CSSH",
    "East Asian Studies": "CSSH", "Latin American Studies": "CSSH",
    "African-American Studies": "CSSH", "Women's Studies": "CSSH",
    "Women": "CSSH", "Social Science": "CSSH",
    "Public Policy": "CSSH", "Public Administration": "CSSH",
    "Urban Studies": "CSSH", "Humanities": "CSSH",
    "Education": "Professional Studies", "Professional Studies": "Professional Studies",
    "Col of Professional Studies": "Professional Studies",
    "Counseling & Educational Psych": "Professional Studies",
    "Counseling amp Educational Psych": "Professional Studies",
    "Counseling  Educational Psych": "Professional Studies",
    "Law": "Law",
}


def get_college(dept):
    if not isinstance(dept, str):
        return "Other"
    return COLLEGE_MAP.get(dept, "Other")


def chunk_insert(cur, sql, rows, page_size=5000):
    for i in range(0, len(rows), page_size):
        execute_values(cur, sql, rows[i:i + page_size])


def swap_in(conn, table):
    """Replace <table> with the freshly-built <table>_new without a
    missing-table window.

    CRDB v25.1+ autocommits before every DDL (autocommit_before_ddl=on), so a
    DROP+CREATE+INSERT rebuild exposes live readers to a missing/empty table and
    a crash mid-rebuild leaves no table at all. Instead: build into _new, then
    swap via two renames committed together (renames are metadata-only and
    allowed transactionally once the DDL autocommit is off for the session).
    A stray _old/_new from a crashed run is cleaned by the next run's DROPs.
    """
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {table}_old")
    conn.commit()
    try:
        cur.execute("SET autocommit_before_ddl = off")
        conn.commit()
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,)
        )
        if cur.fetchone():
            cur.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        cur.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        conn.commit()  # both renames land together — no missing-table window
    except Exception as e:
        conn.rollback()
        cur = conn.cursor()
        # Fallback: per-statement renames (millisecond window, still crash-safe
        # — worst case is a stray _old plus one rename to redo, never a
        # missing table for more than an instant).
        print(f"  swap_in: transactional swap failed for {table} ({e}); using per-statement renames")
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,)
        )
        if cur.fetchone():
            cur.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            conn.commit()
        cur.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        conn.commit()
    finally:
        try:
            cur.execute("RESET autocommit_before_ddl")
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  swap_in: RESET autocommit_before_ddl failed for {table} ({e}); "
                  "later DDL in this run may fail")
    cur.execute(f"DROP TABLE IF EXISTS {table}_old")
    conn.commit()
    cur.close()


def apply_counted_num_ratings(rmp_profs, review_keys):
    """Replace RMP's numRatings counter with the ratings we actually hold.

    numRatings is a denormalised aggregate RMP does not recalculate when a rating
    is added or removed, so it disagrees with the rating nodes RMP serves for 392
    professors — 376 low (by 1-3 apiece) and 16 high, 5 of whom claim a rating and
    serve none. Everything downstream counts from this field (total_reviews, the
    GOATED review floor, the shrinkage weight), so trusting
    the counter meant the displayed count disagreed with the reviews listed.

    Must run after merge_rmp_aliases — that folds RMP's duplicate profile pages
    onto one _name_key, and the reviews from all of them carry that same key — and
    before total_reviews is derived. Modifies rmp_profs in place; returns how many
    professors were corrected.
    """
    if rmp_profs.empty:
        return 0
    counts = pd.Series(list(review_keys), dtype=object).value_counts()
    before = pd.to_numeric(rmp_profs["num_ratings"], errors="coerce").fillna(0).astype(int)
    rmp_profs["num_ratings"] = (
        rmp_profs["_name_key"].map(counts).fillna(0).astype(int))
    return int((rmp_profs["num_ratings"] != before).sum())


def apply_counted_rmp_rating(rmp_profs, review_keys, review_quality):
    """Recompute `rating` as the mean of the ratings we actually hold.

    The partner of apply_counted_num_ratings, so `rating` and num_ratings
    describe one set of rows. It also supersedes the counter-weighted average merge_rmp_aliases
    builds across an RMP professor's duplicate profile pages — the stored ratings
    from all of those pages already carry the merged key, so averaging them is
    the same quantity measured directly instead of reconstructed.

    Quality outside 1-5 is a missing score rather than a score of zero, so it is
    dropped from the mean — but the rating node still exists, so it stays in the
    count. A professor with no usable quality keeps whatever RMP reported; there
    is no measurement to replace it with. Modifies rmp_profs in place; returns
    how many means moved.
    """
    if rmp_profs.empty:
        return 0
    quality = pd.DataFrame({
        "k": list(review_keys),
        "q": pd.to_numeric(pd.Series(list(review_quality)), errors="coerce"),
    }).dropna()
    quality = quality[(quality["q"] >= 1) & (quality["q"] <= 5)]
    means = quality.groupby("k")["q"].mean()
    before = pd.to_numeric(rmp_profs["rating"], errors="coerce")
    counted = rmp_profs["_name_key"].map(means)
    rmp_profs["rating"] = counted.where(counted.notna(), before)
    after = rmp_profs["rating"]
    # NaN != NaN in pandas, so a professor who had no rating before and has none
    # now would otherwise be reported as corrected.
    same = (after == before) | (after.isna() & before.isna())
    return int((~same).sum())


# A review's `course` is free text typed on RMP: "CS2500", "cs 2500", "CS-2500",
# a bare "2301", or noise ("DUEPROCESS", "ECON115", "HISTIDK", "ACCOUNTING2301",
# "BEFORE1400"). Only a Northeastern subject (two to four letters) plus exactly
# four digits is a code we can send a student to.
COURSE_CODE_RE = re.compile(r"^([A-Z]{2,4})(\d{4})$")
BARE_NUMBER_RE = re.compile(r"^\d{4}$")


def normalize_course_code(raw):
    """Canonical "SUBJ1234" from a review's course text, or None.

    Uppercased with spaces and hyphens removed. A bare number returns None here;
    it needs the professor's other reviews to resolve (resolve_course_codes).
    """
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = re.sub(r"[\s\-]+", "", str(raw)).upper()
    return s if COURSE_CODE_RE.match(s) else None


def resolve_course_codes(prof_keys, courses):
    """Course code for each review, from parallel (professor key, course text) lists.

    A bare number ("2301") is a code with the subject left off. It resolves to
    SUBJ2301 only when the same professor has another review whose code carries
    that number under exactly one subject; with none, or with two ("MGSC2301" and
    "SCHM2301" both taught), the subject is a guess and the answer is None.

    Returns a list aligned with the inputs.
    """
    prof_keys, courses = list(prof_keys), list(courses)
    codes = [normalize_course_code(c) for c in courses]
    subjects = {}  # (professor, number) -> {subject}
    for pk, code in zip(prof_keys, codes):
        if code:
            m = COURSE_CODE_RE.match(code)
            subjects.setdefault((pk, m.group(2)), set()).add(m.group(1))
    out = []
    for pk, raw, code in zip(prof_keys, courses, codes):
        if code is None and raw is not None and not (isinstance(raw, float) and np.isnan(raw)):
            bare = re.sub(r"[\s\-]+", "", str(raw))
            if BARE_NUMBER_RE.match(bare):
                subs = subjects.get((pk, bare), set())
                if len(subs) == 1:
                    code = next(iter(subs)) + bare
        out.append(code)
    return out


def build_course_catalog_rows(records, catalog, previous):
    """course_catalog rows from (course_code, quality, department) review records.

    One row per code that at least one review resolves to, as (code, name,
    department, search_text, avg_rating, num_ratings). avg_rating is the mean of
    the quality scores in 1-5 (None when there are none) and num_ratings counts
    every review, matching how professors_catalog counts ratings.

    name and department come from `catalog` (catalog_courses: code -> (name,
    department)) first, then `previous` (the last course_catalog, so a name once
    known is not lost when catalog_courses is absent), else the name is None and
    the department is the most common one among the code's reviews. search_text
    is the lowercased code plus the name, which is what the course search reads.
    """
    quality = {}   # code -> [scores]
    counts = {}    # code -> number of reviews
    depts = {}     # code -> {department: n}
    for code, q, dept in records:
        if not code:
            continue
        counts[code] = counts.get(code, 0) + 1
        q = pd.to_numeric(q, errors="coerce")
        if pd.notna(q) and 1 <= q <= 5:
            quality.setdefault(code, []).append(float(q))
        d = "" if dept is None else str(dept).strip()
        if d and d.lower() != "nan":
            depts.setdefault(code, {}).setdefault(d, 0)
            depts[code][d] += 1

    def clean(v):
        v = "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip()
        return v or None

    rows = []
    for code in sorted(counts):
        name = dept = None
        for source in (catalog, previous):
            if code in source:
                src_name, src_dept = source[code]
                name, dept = clean(src_name), clean(src_dept)
                break
        if not dept and code in depts:
            dept = sorted(depts[code].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        scores = quality.get(code)
        avg = round(sum(scores) / len(scores), 2) if scores else None
        search_text = " ".join([code.lower()] + ([name.lower()] if name else []))
        rows.append((code, name, dept or "", search_text, avg, counts[code]))
    return rows


# professors_catalog column order, mirroring the INSERT in main(); the test suite
# pins the two together.
CATALOG_COLUMNS = (
    "slug", "name", "name_key", "department", "college", "avg_rating",
    "rmp_rating", "num_ratings", "total_reviews", "would_take_again_pct",
    "difficulty", "professor_url", "image_url", "focus_x", "focus_y",
    "total_comments",
)


def apply_avg_rating(rmp_profs):
    """Write avg_rating in place: the RMP rating, NULL when there are no ratings."""
    has_rmp = (rmp_profs["num_ratings"] > 0) & (rmp_profs["rating"] > 0)
    rmp_profs["avg_rating"] = np.where(has_rmp, rmp_profs["rating"].round(2), np.nan)
    rmp_profs["avg_rating"] = rmp_profs["avg_rating"].where(
        rmp_profs["avg_rating"].notna(), other=None)


def assign_slugs(name_keys, existing):
    """Slug per name_key, in order.

    A professor already in professors_catalog keeps the slug it has (`existing`:
    name_key -> slug), so links and bookmarks survive a rebuild. Preserved slugs
    are reserved first, so a new professor can never take one. Everyone else gets
    name_to_slug(name_key), suffixed -2, -3... on a collision.
    """
    name_keys = list(name_keys)
    used = set()
    slugs = {}
    for nk in name_keys:
        slug = existing.get(nk)
        if slug and slug not in used:
            slugs[nk] = slug
            used.add(slug)
    for nk in name_keys:
        if nk in slugs:
            continue
        base = slug = name_to_slug(nk)
        n = 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        slugs[nk] = slug
        used.add(slug)
    return [slugs[nk] for nk in name_keys]


def _table_exists(cur, table):
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s", (table,))
    return cur.fetchone() is not None


def _read_lookup(conn, table, columns):
    """{first column: rest of the row} from <table>, or {} when it does not exist."""
    cur = conn.cursor()
    try:
        if not _table_exists(cur, table):
            return {}
        cur.execute(f"SELECT {', '.join(columns)} FROM {table}")
        return {r[0]: tuple(r[1:]) for r in cur.fetchall()}
    except Exception as e:
        conn.rollback()
        print(f"  Could not read {table} ({e}); building without it")
        return {}
    finally:
        cur.close()


def main(csv_dir=None):
    conn = _connect()

    # Read from local CSVs (much faster than downloading from CRDB)
    csv_dir = csv_dir or os.path.join(os.path.dirname(__file__), "Better_Scraper", "output_data")
    print("Loading from local CSVs...")
    rmp_profs = pd.read_csv(os.path.join(csv_dir, "rmp_professors.csv"))
    print(f"  rmp_professors: {len(rmp_profs)}")
    rmp_reviews = pd.read_csv(os.path.join(csv_dir, "rmp_reviews.csv"))
    print(f"  rmp_reviews: {len(rmp_reviews)}")
    photos = pd.read_csv(os.path.join(csv_dir, "professor_photos.csv"))
    print(f"  professor_photos: {len(photos)}")

    # ── Photo lookup ──
    photos["_key"] = photos["name"].astype(str).apply(normalize_name)
    photos["_url"] = photos["image_url"].astype(str).apply(upgrade_image_url)
    photo_lookup = dict(zip(photos["_key"], photos["_url"]))
    # Also map alias sources → canonical targets so both names find the photo
    for alias_src, alias_tgt in ALIAS_MAP.items():
        if alias_src in photo_lookup and alias_tgt not in photo_lookup:
            photo_lookup[alias_tgt] = photo_lookup[alias_src]
        elif alias_tgt in photo_lookup and alias_src not in photo_lookup:
            photo_lookup[alias_src] = photo_lookup[alias_tgt]

    # ── Focus lookups (default 50/30 when missing) ──
    def _focus_col(col):
        if col in photos.columns:
            return pd.to_numeric(photos[col], errors="coerce")
        return pd.Series([np.nan] * len(photos))

    photos["_fx"] = _focus_col("focus_x").fillna(50.0)
    photos["_fy"] = _focus_col("focus_y").fillna(30.0)
    focus_x_lookup = dict(zip(photos["_key"], photos["_fx"]))
    focus_y_lookup = dict(zip(photos["_key"], photos["_fy"]))
    for alias_src, alias_tgt in ALIAS_MAP.items():
        for lk in (focus_x_lookup, focus_y_lookup):
            if alias_src in lk and alias_tgt not in lk:
                lk[alias_tgt] = lk[alias_src]
            elif alias_tgt in lk and alias_src not in lk:
                lk[alias_src] = lk[alias_tgt]

    # ── Clean RMP data ──
    rmp_profs["rating"] = pd.to_numeric(rmp_profs["rating"], errors="coerce")
    rmp_profs["num_ratings"] = pd.to_numeric(rmp_profs["num_ratings"], errors="coerce")
    rmp_profs.dropna(subset=["rating", "num_ratings"], inplace=True)
    rmp_profs["name"] = rmp_profs["name"].astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    rmp_profs["department"] = rmp_profs["department"].astype(str).str.replace(r'\bamp\b', '&', regex=True)

    # ── Merge RMP aliases ──
    # ── Merge RMP aliases ──
    def merge_rmp_aliases(df):
        df["_name_key"] = df["name"].apply(normalize_name)
        df["_name_key"] = df["_name_key"].replace(ALIAS_MAP)
        rows = []
        for nk, g in df.groupby("_name_key"):
            if len(g) == 1:
                rows.append(g.iloc[0])
                continue
            g = g.sort_values("num_ratings", ascending=False)
            primary = g.iloc[0].copy()
            tot = g["num_ratings"].sum()
            if tot > 0:
                primary["rating"] = (g["rating"] * g["num_ratings"]).sum() / tot
                if "level_of_difficulty" in g.columns:
                    diffs = pd.to_numeric(g["level_of_difficulty"], errors="coerce")
                    if diffs.notna().any():
                        primary["level_of_difficulty"] = (diffs.fillna(0) * g["num_ratings"]).sum() / g.loc[diffs.notna(), "num_ratings"].sum()
                if "would_take_again_pct" in g.columns:
                    wtas = pd.to_numeric(
                        g["would_take_again_pct"].astype(str).str.replace("%", "").replace({"N/A": None, "": None}),
                        errors="coerce"
                    )
                    if wtas.notna().any():
                        val = (wtas.fillna(0) * g["num_ratings"]).sum() / g.loc[wtas.notna(), "num_ratings"].sum()
                        primary["would_take_again_pct"] = f"{round(val, 1)}%"
            primary["num_ratings"] = tot
            primary["name"] = nk.title()
            rows.append(primary)
        return pd.DataFrame(rows).reset_index(drop=True)


    rmp_profs = merge_rmp_aliases(rmp_profs)
    rmp_profs["college"] = rmp_profs["department"].apply(get_college)

    # ── Data-deletion requests ──
    # Dropped here, once the name_key exists and before anything is derived from
    # it, so one filter covers every product keyed on a professor:
    # professors_catalog and course_catalog. Filtering later would leave a
    # professor out of the catalog while their rows still built it.
    #
    # This is the enforcement point that matters most, because it is the one that
    # runs every refresh. A row deleted by hand comes back with the next rebuild;
    # a row dropped here never enters it. See denylist.py.
    rmp_rev_keys = rmp_reviews["professor_name"].apply(normalize_name).replace(ALIAS_MAP)
    if denied_hashes():
        rmp_before = len(rmp_profs)
        rmp_profs = rmp_profs[~rmp_profs["_name_key"].map(is_denied_key)].copy()
        print(f"Denylist: dropped {rmp_before - len(rmp_profs)} RMP professors "
              f"({len(denied_hashes())} entries)")

    # RMP's numRatings counter is a stale aggregate, so count the ratings we
    # actually hold instead. Runs before total_reviews and avg_rating, both of
    # which read this field.
    recounted = apply_counted_num_ratings(rmp_profs, rmp_rev_keys)
    print(f"Recounted num_ratings from stored ratings: {recounted} professors corrected")
    # And the mean over the same rows, so `rating` and num_ratings describe one
    # population. Must precede apply_avg_rating, which reads `rating`.
    remeaned = apply_counted_rmp_rating(rmp_profs, rmp_rev_keys, rmp_reviews["quality"])
    print(f"Recomputed rmp rating from stored ratings: {remeaned} professors corrected")

    rmp_profs["total_reviews"] = rmp_profs["num_ratings"].astype(int)

    apply_avg_rating(rmp_profs)

    # ── Comment counts per name_key ──
    rmp_rev = rmp_reviews[rmp_reviews["comment"].notna() & (rmp_reviews["comment"].astype(str).str.strip() != "")].copy()
    rmp_rev["_name_key"] = rmp_rev["professor_name"].apply(normalize_name).replace(ALIAS_MAP)
    rmp_comment_counts_lookup = rmp_rev.groupby("_name_key").size().astype(int).to_dict()
    print(f"Computed comment counts for {len(rmp_comment_counts_lookup)} professors")

    # ── Build catalog rows ──
    # A professor already in the catalog keeps the slug it has.
    existing_slugs = {nk: slug for nk, (slug,) in _read_lookup(
        conn, "professors_catalog", ("name_key", "slug")).items()}
    slugs = assign_slugs(rmp_profs["_name_key"], existing_slugs)
    print(f"Preserved {sum(1 for nk in rmp_profs['_name_key'] if nk in existing_slugs)} existing slugs")
    catalog_rows = []

    for (_, row), slug in zip(rmp_profs.iterrows(), slugs):
        has_rmp = int(row["num_ratings"]) > 0 and float(row["rating"]) > 0
        dept = str(row["department"])
        college = get_college(dept)

        wta = None
        wta_raw = str(row.get("would_take_again_pct", "")).strip().replace("%", "")
        try:
            if wta_raw and wta_raw.lower() not in ("nan", "n/a", ""):
                wta = round(float(wta_raw), 1)
                if wta < 0:
                    wta = None
        except (ValueError, TypeError):
            pass

        difficulty = None
        if "level_of_difficulty" in row.index:
            try:
                val = float(row["level_of_difficulty"])
                if pd.notna(val) and val > 0:
                    difficulty = round(val, 2)
            except (ValueError, TypeError):
                pass

        catalog_rows.append((
            slug, row["name"], row["_name_key"], dept, college,
            float(row["avg_rating"]) if pd.notna(row["avg_rating"]) else None,
            round(float(row["rating"]), 2) if has_rmp else None,
            int(row["num_ratings"]), int(row["total_reviews"]),
            wta, difficulty,
            (row["professor_url"] if isinstance(row.get("professor_url"), str) and row["professor_url"] else None),
            photo_lookup.get(row["_name_key"], None),
            float(focus_x_lookup.get(row["_name_key"], 50.0)),
            float(focus_y_lookup.get(row["_name_key"], 30.0)),
            int(rmp_comment_counts_lookup.get(row["_name_key"], 0)),
        ))

    print(f"Built catalog with {len(catalog_rows)} professors")

    # ── Course codes and course catalog ──
    # Resolved per (professor, course text) so the bare-number rule sees one
    # professor's reviews at a time. The same mapping backfills rmp_reviews below.
    rev_pairs = pd.DataFrame({
        "professor_name": rmp_reviews["professor_name"],
        "course": rmp_reviews["course"],
        "_key": rmp_rev_keys,
    })
    rev_pairs["course_code"] = resolve_course_codes(rev_pairs["_key"], rev_pairs["course"])
    resolved = rev_pairs["course_code"].notna() & ~rev_pairs["_key"].map(is_denied_key)
    course_records = list(zip(
        rev_pairs.loc[resolved, "course_code"],
        rmp_reviews.loc[resolved, "quality"],
        rmp_reviews.loc[resolved, "department"].astype(str).str.replace(r'\bamp\b', '&', regex=True),
    ))
    catalog_names = _read_lookup(conn, "catalog_courses", ("code", "name", "department"))
    previous_courses = _read_lookup(conn, "course_catalog", ("code", "name", "department"))
    course_rows = build_course_catalog_rows(course_records, catalog_names, previous_courses)
    print(f"Built course catalog with {len(course_rows)} courses "
          f"(catalog_courses: {len(catalog_names)}, previous: {len(previous_courses)})")

    # ── Compute stats ──
    stat_professors = len({n.strip() for n in rmp_profs["_name_key"].unique()
                           if isinstance(n, str) and n.strip()})
    stat_courses = len(course_rows)
    stat_comments = len(rmp_reviews)
    stat_departments = rmp_profs["department"].str.lower().str.strip().nunique()

    # ══════════════════════════════════════════════
    #  Write everything to CockroachDB
    # ══════════════════════════════════════════════
    cur = conn.cursor()

    # 1. professors_catalog
    print("Creating professors_catalog...")
    cur.execute("DROP TABLE IF EXISTS professors_catalog_new")
    cur.execute("""
        CREATE TABLE professors_catalog_new (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            department TEXT,
            college TEXT,
            avg_rating FLOAT,
            rmp_rating FLOAT,
            num_ratings INT DEFAULT 0,
            total_reviews INT DEFAULT 0,
            would_take_again_pct FLOAT,
            difficulty FLOAT,
            professor_url TEXT,
            image_url TEXT,
            focus_x FLOAT,
            focus_y FLOAT,
            total_comments INT DEFAULT 0
        )
    """)
    chunk_insert(cur, """
        INSERT INTO professors_catalog_new
        (slug, name, name_key, department, college, avg_rating, rmp_rating,
         num_ratings, total_reviews, would_take_again_pct, difficulty,
         professor_url, image_url, focus_x, focus_y, total_comments)
        VALUES %s
    """, catalog_rows)
    cur.execute("CREATE INDEX idx_pc_name_key ON professors_catalog_new (name_key)")
    cur.execute("CREATE INDEX idx_pc_college ON professors_catalog_new (college)")
    cur.execute("CREATE INDEX idx_pc_dept ON professors_catalog_new (department)")
    conn.commit()
    swap_in(conn, "professors_catalog")
    print(f"  Inserted {len(catalog_rows)} rows")

    # 2. course_catalog
    print("Creating course_catalog...")
    cur.execute("DROP TABLE IF EXISTS course_catalog_new")
    cur.execute("""
        CREATE TABLE course_catalog_new (
            code TEXT PRIMARY KEY,
            name TEXT,
            department TEXT,
            search_text TEXT,
            avg_rating FLOAT,
            num_ratings INT DEFAULT 0
        )
    """)
    chunk_insert(cur, """
        INSERT INTO course_catalog_new
        (code, name, department, search_text, avg_rating, num_ratings)
        VALUES %s
    """, course_rows)
    cur.execute("CREATE INDEX idx_cc_dept ON course_catalog_new (department)")
    cur.execute("CREATE INDEX idx_cc_rating ON course_catalog_new (avg_rating)")
    conn.commit()
    swap_in(conn, "course_catalog")
    print(f"  Inserted {len(course_rows)} courses")

    # 3. stats_cache
    print("Updating stats_cache...")
    cur.execute("CREATE TABLE IF NOT EXISTS stats_cache (key TEXT PRIMARY KEY, value INT)")
    cur.execute(
        "UPSERT INTO stats_cache VALUES ('professors', %s), ('courses', %s), ('comments', %s), ('departments', %s)",
        (stat_professors, stat_courses, stat_comments, stat_departments)
    )
    conn.commit()

    # 4. Add name_key to rmp_reviews (batch via temp table)
    print("Adding name_key to rmp_reviews...")
    conn.close()
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SET experimental_enable_temp_tables = 'on'")

    try:
        cur.execute("ALTER TABLE rmp_reviews ADD COLUMN name_key TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
        cur = conn.cursor()

    cur.execute("SET experimental_enable_temp_tables = 'on'")

    unique_rev_names = rmp_reviews["professor_name"].dropna().unique()
    rev_mapping_rows = []
    for name in unique_rev_names:
        nk = normalize_name(name)
        nk = ALIAS_MAP.get(nk, nk)
        rev_mapping_rows.append((name, nk))

    cur.execute("CREATE TEMP TABLE _rev_nk_map (professor_name TEXT, name_key TEXT)")
    chunk_insert(cur, "INSERT INTO _rev_nk_map (professor_name, name_key) VALUES %s", rev_mapping_rows)
    cur.execute("""
        UPDATE rmp_reviews r SET name_key = m.name_key
        FROM _rev_nk_map m
        WHERE r.professor_name = m.professor_name
    """)
    cur.execute("DROP TABLE _rev_nk_map")
    conn.commit()

    try:
        cur.execute("CREATE INDEX idx_rr_name_key ON rmp_reviews (name_key)")
        conn.commit()
    except Exception:
        conn.rollback()
        cur = conn.cursor()
    print(f"  Updated {len(unique_rev_names)} unique review names")

    # 5. Add course_code to rmp_reviews, from the mapping resolved above.
    # Rewrites rows whose value merely disagrees (IS DISTINCT FROM), not only NULL
    # ones, so a change to the resolution rules repairs old rows on the next run.
    # Idempotent — once the rows agree this matches nothing.
    print("Adding course_code to rmp_reviews...")
    try:
        cur.execute("ALTER TABLE rmp_reviews ADD COLUMN course_code TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
        cur = conn.cursor()

    cur.execute("SET experimental_enable_temp_tables = 'on'")
    code_rows = [
        (name, course, code if isinstance(code, str) else None)
        for name, course, code in rev_pairs.loc[
            rev_pairs["professor_name"].notna() & rev_pairs["course"].notna(),
            ["professor_name", "course", "course_code"]].drop_duplicates().itertuples(index=False)
    ]
    cur.execute("CREATE TEMP TABLE _rev_cc_map (professor_name TEXT, course TEXT, course_code TEXT)")
    chunk_insert(cur, "INSERT INTO _rev_cc_map (professor_name, course, course_code) VALUES %s", code_rows)
    cur.execute("""
        UPDATE rmp_reviews r SET course_code = m.course_code
        FROM _rev_cc_map m
        WHERE r.professor_name = m.professor_name
          AND r.course = m.course
          AND r.course_code IS DISTINCT FROM m.course_code
    """)
    cur.execute("DROP TABLE _rev_cc_map")
    conn.commit()

    try:
        cur.execute("CREATE INDEX idx_rr_course_code ON rmp_reviews (course_code)")
        conn.commit()
    except Exception:
        conn.rollback()
        cur = conn.cursor()
    print(f"  Resolved {len(code_rows)} unique (professor, course) pairs")

    conn.close()
    print(f"\nPrecompute complete!")
    print(f"  {len(catalog_rows)} professors in catalog")
    print(f"  {len(course_rows)} courses in catalog")
    print(f"  Stats: {stat_professors} professors, {stat_courses} courses, {stat_comments} comments, {stat_departments} departments")


if __name__ == "__main__":
    main()
