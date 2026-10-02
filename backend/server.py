"""
Backend API server for NEU Professor Ratings.
No pandas/numpy — queries CockroachDB directly per request.

Install deps:  pip install flask flask-cors flask-limiter psycopg2-binary pyjwt requests python-dotenv gunicorn
Run:           python server.py
"""

import os, re, unicodedata, json, hashlib, random
import html as _html
import psycopg2
import psycopg2.errors
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from functools import lru_cache
from dotenv import load_dotenv
from prof_aliases import ALIAS_MAP
from flask import Flask, g, jsonify, request, redirect, make_response
from flask_cors import CORS
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException
import jwt as pyjwt
import requests as http_requests
from urllib.parse import urlencode, urlparse
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread, Event
import time
from rag.chat_search import keyword_search
from rag.chat_question import handle_question
from rag.llm_adapter import GroqAdapter
from rag.key_pool import KeyPool
from rag.chat_gate import gate
from rag.chat_retrieve import retrieve, fetch_reddit_mentions
from rag.query_embedder import embed_query
from rag.chat_answer import generate, generate_course_list, generate_course_ranking
from professor_full import build_payload
import bookmarks
import moderation
import usage_alert

load_dotenv()

import types as _types

CHAT_ENABLED = os.getenv("CHAT_ENABLED", "false").lower() == "true"
_IP_SALT = os.getenv("ASK_IP_SALT", "rmh-default-salt")
_chat_pool = KeyPool()
_chat_adapter = GroqAdapter(_chat_pool)

def _hash_ip(ip):
    return hashlib.sha256((_IP_SALT + (ip or "")).encode()).hexdigest()[:16]


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────
# Shared with precompute.py, which needs the same ordering to pick a course's
# current title (most recent term wins). Two copies of a parser this fiddly
# would drift, so it lives in one module both import.
from term_order import term_sort_key  # noqa: E402


def normalize_name(name):
    s = str(name).strip().lower()
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# Build a word-level mapping so partial/typeahead queries also resolve.
# e.g. typing "virgiliu" (an RMP-only spelling) still finds "virgil".
_WORD_ALIAS = {}
for _from, _to in ALIAS_MAP.items():
    _from_words = set(_from.split())
    _to_words = set(_to.split())
    for w in _from_words - _to_words:
        # Strip parens/punctuation so "(katherine)" becomes "katherine",
        # "c." becomes "c", matching what normalize_name produces.
        clean = re.sub(r'[^a-z0-9\-]', '', w)
        if clean:
            _WORD_ALIAS[clean] = _to_words - _from_words


def resolve_alias(q):
    """Return the canonical (trace) query if q matches an alias, else q."""
    return ALIAS_MAP.get(q, q)


def sanitize(text: str) -> str:
    return _html.unescape(str(text))


def friendly_count(n: int) -> str:
    if n < 100:
        return str(n)
    rounded = (n // 100) * 100
    return f"{rounded:,}+"


def _name_to_slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


# ──────────────────────────────────────────────
#  App setup
# ──────────────────────────────────────────────
app = Flask(__name__)
app.config["COMPRESS_MIMETYPES"] = ["application/json", "text/html"]
app.config["COMPRESS_MIN_SIZE"] = 256
Compress(app)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
CORS(app, supports_credentials=True, origins=[FRONTEND_URL])
def get_real_ip():
    """Get the real client IP from X-Forwarded-For (set by Vercel/Railway proxy)."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # Last entry is the IP appended by our trusted proxy (Vercel/Railway);
        # earlier entries are client-supplied and spoofable.
        return xff.split(",")[-1].strip()
    return get_remote_address()

limiter = Limiter(get_real_ip, app=app, default_limits=["20 per second"])


BLOCKED_USER_AGENTS = [
    "python-requests", "python-httpx", "python-urllib", "aiohttp",
    "scrapy", "curl", "wget", "go-http-client", "java/", "okhttp",
    "httpie", "postmanruntime", "node-fetch", "undici",
    "gptbot", "ccbot", "claudebot", "bytespider", "google-extended",
    "ahrefsbot", "semrushbot", "dotbot", "mj12bot", "petalbot",
    "barkrowler", "dataforseobot",
]

def _origin_host(value: str) -> str:
    """Normalize a URL to 'scheme://host[:port]', lowercased, or '' if unparseable.

    Strips any path/query (Referer carries a path; Origin does not) so the
    comparison is on the web origin only. Returns '' when scheme or host is
    missing, which never matches the allowlist.
    """
    p = urlparse(value or "")
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}".lower()


# Compare against normalized origins so a stray trailing slash or path on either
# side can't cause a lockout (or a startswith-style prefix bypass).
ALLOWED_ORIGINS = {
    _origin_host(o) for o in (
        FRONTEND_URL,
        "http://localhost:5173",
        "http://localhost:3000",
    ) if _origin_host(o)
}


@app.before_request
def block_bots():
    ua = (request.headers.get("User-Agent") or "").lower()
    if ua and any(bot in ua for bot in BLOCKED_USER_AGENTS):
        return jsonify({"error": "Forbidden"}), 403


# Shared secret added by the Vercel middleware proxy (frontend/middleware.ts).
# Unset = open (local dev / pre-rollout); set on Railway to lock the backend
# to Vercel-proxied traffic only.
PROXY_SECRET = os.getenv("PROXY_SECRET")


@app.before_request
def require_proxy_key():
    if not PROXY_SECRET:
        return
    if request.headers.get("X-Proxy-Key") != PROXY_SECRET:
        return jsonify({"error": "Forbidden"}), 403


@app.before_request
def check_origin():
    """Block API requests that don't originate from the frontend."""
    if request.path.startswith("/api/auth/google"):
        return  # allow OAuth redirects
    if request.method == "OPTIONS":
        return  # allow CORS preflight
    raw = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if not raw:
        return  # no Origin/Referer (e.g. same-origin GET) — nothing to check
    if _origin_host(raw) not in ALLOWED_ORIGINS:
        return jsonify({"error": "Forbidden"}), 403


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.errorhandler(HTTPException)
def _http_error(e):
    """404/405/429 and every other HTTP error as JSON; the frontend reads the status."""
    return jsonify({"error": e.description}), e.code


@app.errorhandler(Exception)
def _unhandled_error(e):
    """Any uncaught error is logged with its traceback and served as a JSON 500."""
    app.logger.exception("Unhandled error on %s", request.path)
    return jsonify({"error": "Internal server error"}), 500


# ──────────────────────────────────────────────
#  Database connection pool
# ──────────────────────────────────────────────
CRDB_DATABASE_URL = os.getenv("CRDB_DATABASE_URL")
if not CRDB_DATABASE_URL:
    raise RuntimeError("CRDB_DATABASE_URL environment variable is required")

_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(5, 10, CRDB_DATABASE_URL, sslmode="require",
                                       connect_timeout=5,
                                       keepalives=1, keepalives_idle=30,
                                       keepalives_interval=10, keepalives_count=3)
    return _pool

# ──────────────────────────────────────────────
#  Simple in-memory cache (TTL-based)
# ──────────────────────────────────────────────
_cache = {}
_cache_lock = Lock()
CACHE_TTL = 3600      # 1 hour
CACHE_MAX_SIZE = 5000

_feedback_lock = Lock()
_feedback_count = 0
_feedback_date = None   # "YYYY-MM-DD" UTC, resets counter each day
FEEDBACK_DAILY_LIMIT = 300
# feedback types that require a reply email and carry the signed-in account's verified sub,
# so support can act on the right ask_log rows (review an appeal, or erase the user's data)
_ACCOUNT_FEEDBACK_TYPES = {"banappeal", "datadeletion"}



def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]
        return None


def cache_set(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}
        if len(_cache) > CACHE_MAX_SIZE:
            cutoff = time.time() - CACHE_TTL
            expired = [k for k, v in _cache.items() if v["ts"] < cutoff]
            for k in expired:
                del _cache[k]


# ──────────────────────────────────────────────
#  Daily memory reset at 09:00 UTC
# ──────────────────────────────────────────────
_shutdown_event = Event()
_reset_thread_started = False

def _seconds_until_next_9utc():
    now = datetime.now(timezone.utc)
    target = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()

def _daily_cache_reset():
    while not _shutdown_event.is_set():
        wait = _seconds_until_next_9utc()
        if _shutdown_event.wait(timeout=wait):
            break
        try:
            with _cache_lock:
                _cache.clear()
            print(f"[{datetime.now(timezone.utc).isoformat()}] Daily cache reset complete",
                  flush=True)
        except Exception as e:
            print(f"[{datetime.now(timezone.utc).isoformat()}] Cache reset error: {e}",
                  flush=True)

def _start_reset_thread():
    global _reset_thread_started
    if not _reset_thread_started:
        _reset_thread_started = True
        t = Thread(target=_daily_cache_reset, daemon=True)
        t.start()


def _acquire_fresh_conn():
    key = id(g._get_current_object() if hasattr(g, '_get_current_object') else g)
    g.db_key = key
    g.db = _get_pool().getconn(key=key)
    return g.db


def _discard_db_conn():
    """Return the current request's connection to the pool and mark it closed."""
    db = g.pop('db', None)
    key = g.pop('db_key', None)
    if db is not None:
        try:
            _get_pool().putconn(db, key=key, close=True)
        except Exception:
            try:
                db.close()
            except Exception:
                pass


def get_db():
    if 'db' not in g:
        return _acquire_fresh_conn()
    conn = g.db
    if conn.closed:
        _discard_db_conn()
        return _acquire_fresh_conn()
    return conn


@app.before_request
def _ensure_reset_thread():
    _start_reset_thread()


@app.teardown_appcontext
def return_db(exc):
    db = g.pop('db', None)
    key = g.pop('db_key', None)
    if db is not None:
        try:
            _get_pool().putconn(db, key=key)
        except KeyError:
            try:
                db.close()
            except Exception:
                pass


def query(sql, params=None):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params or ())
        return cur.fetchall()
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        # Connection was stale — discard it and retry once with a fresh one
        _discard_db_conn()
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params or ())
        return cur.fetchall()


def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def catalog_rows_by_trace_keys(trace_keys):
    """Catalog rows whose TRACE spelling (trace_name_key) is one of `trace_keys`.

    These are fuzzy-matched professors: TRACE files their courses under a name
    that differs from the catalog row's name_key, so a name_key lookup by the
    TRACE spelling misses them. A catalog built before the column existed has
    no such rows, and naming the column there raises, so that returns [] after
    rolling back the aborted transaction for the caller's next query.
    """
    if not trace_keys:
        return []
    placeholders = ",".join(["%s"] * len(trace_keys))
    try:
        return query(
            f"SELECT * FROM professors_catalog WHERE trace_name_key IN ({placeholders})",
            list(trace_keys),
        )
    except psycopg2.errors.UndefinedColumn:
        get_db().rollback()
        return []


def _chat_write(sql, params=None):
    """Write helper for the question path (ask_log INSERTs): execute + commit, never fetch.
    The read-only query()/query_one() call fetchall(), which raises on a non-RETURNING INSERT;
    and the pool is not autocommit, so writes must commit explicitly.
    A logging write must never fail the request that already generated a successful answer:
    retry once on a stale connection (mirrors query()), then swallow and log any further error."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        try:
            _discard_db_conn()
            conn = get_db()
            cur = conn.cursor()
            cur.execute(sql, params or ())
            conn.commit()
        except Exception as e:
            print(f"_chat_write: log write failed after retry, dropping: {e}")
    except Exception as e:
        print(f"_chat_write: log write failed, dropping: {e}")

def _chat_write_rc(sql, params=None):
    """Like _chat_write but returns the executed statement's rowcount."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params or ())
    conn.commit()
    return cur.rowcount

def _write(sql, params=None):
    """Write helper for user-facing mutations (bookmarks add/remove): execute +
    commit, retry once on a stale connection (mirrors query()). Unlike
    _chat_write, this does not swallow errors — a failed bookmark write must
    surface to the caller, not be silently dropped like a best-effort log write."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        _discard_db_conn()
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()



# ──────────────────────────────────────────────
#  Google OAuth config
# ──────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is required")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


# ──────────────────────────────────────────────
#  Search helper
# ──────────────────────────────────────────────
def _professor_search(q, limit=5):
    """Search professors with tiered relevance ranking."""
    # Normalize first: name_key is stored lowercase/ASCII, and CockroachDB LIKE is
    # case-sensitive. Callers like the chat/Ask path pass a raw gate hint ("Wu Chieh"),
    # so without this the LIKE filter (and the lowercase ALIAS_MAP) never match.
    q = normalize_name(q)
    # Resolve full-query alias first (e.g. "virgiliu pavlu" → "virgil pavlu")
    q_resolved = resolve_alias(q)

    words = q_resolved.split()
    if not words:
        return []

    # Expand individual words through the word-level alias map so partial
    # typeahead queries like "virgiliu" also match "virgil pavlu".
    expanded_words = set(words)
    for w in words:
        if w in _WORD_ALIAS:
            expanded_words.update(_WORD_ALIAS[w])

    # Build WHERE: each *original* word must match, OR its alias expansion must
    conditions = []
    params = []
    for word in words:
        alt_words = _WORD_ALIAS.get(word)
        if alt_words:
            group = [word] + list(alt_words)
            conditions.append("(" + " OR ".join("name_key LIKE %s" for _ in group) + ")")
            params.extend(f"%{w}%" for w in group)
        else:
            conditions.append("name_key LIKE %s")
            params.append(f"%{word}%")

    where = " AND ".join(conditions)
    rows = query(
        f"SELECT slug, name, name_key, department, avg_rating, total_reviews "
        f"FROM professors_catalog WHERE {where} "
        f"ORDER BY total_reviews DESC LIMIT 100",
        params
    )

    # Rank in Python for proper tiered relevance (use resolved query)
    q_rank = q_resolved
    words_rank = q_resolved.split()

    def rank_match(row):
        nk = row['name_key']
        parts = nk.split()

        # Tier 1: q matches a whole name part exactly
        if q_rank in parts:
            return 1
        # Tier 2: q matches the start of any name part
        if any(p.startswith(q_rank) for p in parts):
            return 2
        # Tier 3: q is a substring of the full name
        if q_rank in nk:
            return 3
        # Tier 4: multi-word: each word prefixes a name part
        if len(words_rank) >= 2 and all(any(p.startswith(w) for p in parts) for w in words_rank):
            return 4
        # Tier 5: multi-word: each word is substring of any name part
        if len(words_rank) >= 2 and all(any(w in p for p in parts) for w in words_rank):
            return 5
        return 6

    ranked = sorted(rows, key=lambda r: (rank_match(r), -(r['total_reviews'] or 0)))
    return ranked[:limit]


# ──────────────────────────────────────────────
#  API Routes
# ──────────────────────────────────────────────
@app.route("/api/stats")
def stats():
    cached = cache_get("stats")
    if cached:
        resp = jsonify(cached)
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp
    rows = query("SELECT key, value FROM stats_cache")
    stat_map = {r['key']: r['value'] for r in rows}
    result = [
        {"label": "Professors", "value": friendly_count(stat_map.get('professors', 0))},
        {"label": "Courses", "value": friendly_count(stat_map.get('courses', 0))},
        {"label": "Comments", "value": friendly_count(stat_map.get('comments', 0))},
        {"label": "Departments", "value": friendly_count(stat_map.get('departments', 0))},
    ]
    cache_set("stats", result)
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/api/colleges")
def colleges():
    cached = cache_get("colleges")
    if cached:
        return jsonify(cached)
    rows = query("""
        SELECT college, COUNT(*) as cnt FROM professors_catalog
        WHERE avg_rating IS NOT NULL
        GROUP BY college HAVING COUNT(*) >= 5
        ORDER BY college
    """)
    result = [r['college'] for r in rows if r['college'] != 'Other']
    cache_set("colleges", result)
    return jsonify(result)


# ── Leaderboard ranking ──────────────────────────────────────────────
# Ranking by raw avg_rating lets a 5.00 built on 3 reviews outrank a 4.9 built on
# 500. 276 professors sit at exactly 5.00 and 95% of them have under 15 reviews,
# which is why the Law and Professional Studies boards (which used to carry a
# review floor of 5 rather than 100) filled up with them.
#
# So rank on a Bayesian posterior mean instead — the IMDb Top 250 formula —
# blending each professor toward the global mean with a prior worth SHRINKAGE_M
# reviews:   (n*R + m*C) / (n + m)
#
# A professor with 3 reviews is dragged to C; one with 500 barely moves. m=50 is
# small enough that the correlation between score and review count only rises to
# ~0.26 (it saturates around 0.30, so a larger m buys nothing but distortion).
# C is read from the data rather than hardcoded so it tracks re-scrapes.
#
# This is the *sort key only* — the displayed number stays avg_rating. Shrinkage
# is mathematically a contraction, so it cannot spread the top of the board; the
# clustering near 4.9 is a real property of the ratings, not something a formula
# should paper over.
SHRINKAGE_M = 50

# What C is measured over. It was avg(avg_rating) across the entire catalog,
# which is mostly professors carrying a handful of responses: their ratings are
# sampling noise, so averaging them describes the noise rather than the
# professors, and the prior the whole board shrinks toward came out of it.
#
# The prior of a ranking should be the mean of the quantity being ranked, so it
# is measured over professors whose rating is actually pinned down. At 30
# responses the standard error of a TRACE mean is ~0.13; at 5 it is ~0.33, which
# is wider than the entire top of the board.
#
# Deliberately global rather than per-college: a per-college prior ranks each
# professor against their own college, which is department-relative ranking under
# another name. That was built once and rejected — this board is a class-picking
# tool, not a per-department award.
RANKING_PRIOR_MIN_REVIEWS = 30
FALLBACK_PRIOR = 4.2   # only for an empty or brand-new catalog

# The board's evidence floor, and deliberately the same 30 as the prior's: a
# threshold for "well enough measured to be ranked" cannot sensibly be looser
# than the one for "well enough measured to inform the prior". The standard-error
# argument above is the whole reason for both.
#
# It replaced a floor of 100 with a per-college exception dropping Law and
# Professional Studies to 5. Both halves of that were wrong in opposite
# directions, and shrinkage is what makes a single number workable:
#
#   - 100 was doing almost no work. Reaching Khoury's rank-10 score needs ~57
#     reviews even at a perfect 5.00, so the shrunk score already excludes
#     nearly everyone the floor was excluding. Re-measured after the
#     total_reviews rebuild, going 100 -> 30 changes one row across every
#     100-floor board: Business rank 10, Peggy O'Kelly -> Laura Huang. (Before
#     the rebuild it changed nothing at all, so expect this to keep drifting
#     with the corpus — the argument is that the floor is near-redundant, not
#     that it is exactly redundant.)
#   - 5 was the actual bug. Law survives it on population (232 eligible), but
#     Professional Studies had 15, so a "top 10" showed two thirds of the
#     department including professors with 7 ratings, scoring within 0.04 of each
#     other and of the prior — an ordering that reflects the prior, not the
#     professors. At 30 that board is 5 professors long, which is the honest
#     answer: the department does not have ten well-measured ones.
#
# So a short board is a feature. Do not backfill it to reach `limit`.
BOARD_MIN_REVIEWS = 30

# CockroachDB has no implicit int/float coercion, so total_reviews (INT) needs an
# explicit cast against avg_rating (FLOAT) or the query fails with
# "unsupported binary operator: <int> * <float>".
# float(m), not "{m}.0" — the latter renders "0.9.0" and is a syntax error if
# SHRINKAGE_M is ever tuned to a non-integer.
# The prior arrives as a bound parameter rather than a subquery, so the sort does
# not carry a full-table aggregate and the value can be measured under its own
# filter.
# The prior is cast explicitly: CockroachDB infers placeholder types from
# context, and a bare %s inside an ORDER BY arithmetic expression is the kind of
# position where it gives up with "could not determine data type of placeholder".
RANKING_SCORE_SQL = """
    ((total_reviews::float * avg_rating + {m} * %s::float)
     / (total_reviews::float + {m}))
""".format(m=float(SHRINKAGE_M))


def ranking_prior(query_one):
    """Mean rating of professors well-enough measured to have one.

    Falls back to a constant only when nothing clears the floor, which means an
    empty catalog — ranking on None would order the whole board by review count.
    """
    row = query_one("""
        SELECT avg(avg_rating) AS prior FROM professors_catalog
        WHERE avg_rating IS NOT NULL AND total_reviews >= %s
    """, (RANKING_PRIOR_MIN_REVIEWS,))
    prior = row.get("prior") if row else None
    return float(prior) if prior is not None else FALLBACK_PRIOR


def shrunk_score(avg_rating, total_reviews, prior_mean, m=SHRINKAGE_M):
    """Python mirror of RANKING_SCORE_SQL (for tests and any Python-side ranking)."""
    if avg_rating is None:
        return None
    n = total_reviews or 0
    return (n * avg_rating + m * prior_mean) / (n + m)


@app.route("/api/goat-professors")
def goat_professors():
    college = request.args.get("college", "Khoury")
    limit = min(int(request.args.get("limit", "10")), 50)
    min_reviews = int(request.args.get("min_reviews", str(BOARD_MIN_REVIEWS)))

    # v5: the ordering has changed three times — to the shrunk score, again when
    # the prior stopped being the whole-catalog average, and again when the
    # per-college review floor collapsed to a single BOARD_MIN_REVIEWS and the
    # sort gained a name tiebreak. An unbumped key serves the previous version
    # from the cache after deploy, which looks exactly like the fix not working.
    cache_key = f"goat:v5:{college}:{limit}:{min_reviews}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    prior = ranking_prior(query_one)
    # `name` breaks ties last. Without it, professors equal on both score and
    # review count come back in whatever order the scan produces, so the board
    # could reshuffle between requests. Measured on the current corpus:
    # 64 such groups across the catalog, covering 137 professors, and none
    # reaching a top 10. So this closes the door rather than fixing a visible bug.
    #
    # Which colleges hold them is not worth stating: an earlier version of this
    # comment said "none in Law", Law acquired one on the next refresh, and the
    # test guarding the claim went red on data drift alone. Tie groups move
    # whenever total_reviews is recomputed. The durable property is the one
    # test_measured_claims checks — that no tie reaches a board's top 10, which
    # is the only place the ordering is visible.
    rows = query(f"""
        SELECT * FROM professors_catalog
        WHERE college = %s AND total_reviews >= %s
        ORDER BY {RANKING_SCORE_SQL} DESC NULLS LAST, total_reviews DESC, name
        LIMIT %s
    """, (college, min_reviews, prior, limit))

    # Comment counts for the displayed rows, one batched round trip.
    comment_counts = {}
    if rows:
        name_keys = [row["name_key"] for row in rows]
        for r in query(
            "SELECT name_key, COUNT(*) AS cnt FROM rmp_reviews "
            f"WHERE name_key IN ({','.join(['%s'] * len(name_keys))}) "
            "AND comment IS NOT NULL AND comment != '' "
            "GROUP BY name_key",
            name_keys,
        ):
            comment_counts[r["name_key"]] = int(r["cnt"])

    result = []
    for row in rows:
        result.append({
            "name": row["name"],
            "dept": row["department"],
            "rmpRating": round(row["rmp_rating"], 2) if row["rmp_rating"] else None,
            "avgRating": round(row["avg_rating"], 2) if row["avg_rating"] else None,
            # Displayed as "Ratings": the floor above gates on it and
            # RANKING_SCORE_SQL weights by it.
            "totalReviews": row["total_reviews"] or 0,
            "totalComments": comment_counts.get(row["name_key"], 0),
        })
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/random-professor")
def random_professor():
    count_row = query_one("SELECT COUNT(*) as cnt FROM professors_catalog WHERE num_ratings >= 3")
    total = count_row["cnt"] if count_row else 0
    if total == 0:
        return jsonify({"error": "No professors found"}), 404
    offset = random.randint(0, total - 1)
    row = query_one("""
        SELECT * FROM professors_catalog
        WHERE num_ratings >= 3
        LIMIT 1 OFFSET %s
    """, (offset,))
    if not row:
        return jsonify({"error": "No professors found"}), 404
    return jsonify({
        "name": row["name"],
        "dept": row["department"],
        "college": row["college"],
    })


def _format_course_code(raw: str) -> str:
    return re.sub(r"\s+", "", str(raw).upper())


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@app.route("/api/search")
def search():
    q = normalize_name(request.args.get("q", ""))
    search_type = request.args.get("type", "Professor")
    limit = min(int(request.args.get("limit", "5")), 20)

    if len(q) < 2:
        return jsonify([])

    if search_type == "Professor":
        matches = _professor_search(q, limit)
        results = []
        for r in matches:
            results.append({
                "type": "professor",
                "name": r["name"],
                "dept": r["department"],
                "rating": round(r["avg_rating"], 2) if r["avg_rating"] and r["avg_rating"] > 0 else None,
                "slug": r["slug"],
            })
        return jsonify(results)

    else:
        # Course search
        rows = query("""
            SELECT code, name, department FROM course_catalog
            WHERE search_text LIKE %s
            ORDER BY
                CASE WHEN lower(code) LIKE %s THEN 0 ELSE 1 END,
                code
            LIMIT %s
        """, (f"%{q}%", f"{q}%", limit))

        results = []
        for r in rows:
            results.append({
                "type": "course",
                "code": r["code"],
                "name": r["name"],
                "dept": r["department"],
            })
        return jsonify(results)


# ──────────────────────────────────────────────
#  Reddit RAG chatbot
# ──────────────────────────────────────────────
@limiter.limit("30 per minute")
@app.route("/api/chat")
def chat():
    q = (request.args.get("q") or "").strip()
    mode = request.args.get("mode", "keyword")
    if len(q) < 2:
        return jsonify({"mode": mode, "results": []})
    if mode == "keyword":
        try:
            limit = min(int(request.args.get("limit", "20")), 50)
        except (TypeError, ValueError):
            limit = 20
        data = keyword_search(q, query, _professor_search, limit=limit,
                              mod_filter=moderation.sql_filter("t"))
        return jsonify({"mode": "keyword", "results": data["comments"], "professors": data["professors"]})
    # 'question' mode is account-gated: identity comes from the verified JWT (not a spoofable
    # header), so the abuse ladder keys on a server-trusted user id that can't be forged or omitted.
    token = _get_auth_token()
    if not token:
        return jsonify({"mode": "error", "message": "Sign in to use Ask mode."}), 401
    try:
        _claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
        return jsonify({"mode": "error", "message": "Sign in to use Ask mode."}), 401
    deps = _types.SimpleNamespace(
        chat_enabled=CHAT_ENABLED,
        num_keys=len(_chat_pool.entries) or 1,
        query_fn=query,
        query_one_fn=query_one,
        prof_search_fn=_professor_search,
        cache_get_fn=cache_get,
        cache_set_fn=cache_set,
        keyword_search_fn=lambda qq: keyword_search(qq, query, _professor_search),
        gate_fn=lambda qq: gate(qq, _chat_adapter),
        retrieve_fn=lambda qq, hint: retrieve(qq, hint, query, query_one, _professor_search, embed_query_fn=embed_query),
        generate_fn=lambda qq, blocks: generate(qq, blocks, _chat_adapter),
        generate_course_list_fn=lambda topic, courses: generate_course_list(topic, courses, _chat_adapter),
        generate_course_ranking_fn=lambda subject, metric, direction, courses: generate_course_ranking(subject, metric, direction, courses, _chat_adapter),
        log_fn=_chat_write,
        usage_alert_fn=lambda: usage_alert.maybe_alert(
            query, query_one, _chat_write_rc, len(_chat_pool.entries) or 1),
    )
    session_token = _claims.get("sub")  # verified account id — unspoofable, never None
    if not session_token:
        return jsonify({"mode": "error", "message": "Sign in to use Ask mode."}), 401
    ip_hash = _hash_ip(request.headers.get("CF-Connecting-IP", request.remote_addr))
    payload, code = handle_question(q, session_token, ip_hash, deps)
    return jsonify(payload), code


def _department_colleagues(department, exclude_slug):
    """Up to 8 other professors_catalog rows in the same department, highest
    review-count first. Cached per department (shared across every professor
    in it) since professors_catalog is indexed on department already."""
    if not department:
        return []
    cache_key = f"colleagues:{department}"
    rows = cache_get(cache_key)
    if rows is None:
        rows = query("""
            SELECT name, slug, avg_rating, total_reviews FROM professors_catalog
            WHERE department = %s AND slug IS NOT NULL AND total_reviews >= 1
            ORDER BY total_reviews DESC LIMIT 9
        """, (department,))
        cache_set(cache_key, rows)
    colleagues = []
    for r in rows:
        if r["slug"] == exclude_slug:
            continue
        colleagues.append({
            "name": r["name"],
            "slug": r["slug"],
            "avgRating": round(r["avg_rating"], 2) if r["avg_rating"] else None,
            "totalRatings": r["total_reviews"],
        })
    return colleagues[:8]


# ──────────────────────────────────────────────
#  Professor profile page
# ──────────────────────────────────────────────
def professor_payload(slug):
    """The §4.2 payload for `slug` (cached), or None when no professor matches.
    Shared by /full and render.py so the crawler page and the app agree."""
    cache_key = f"prof_full:{slug}"
    data = cache_get(cache_key)
    if data is None:
        data = build_payload(slug, query, query_one, sanitize, fetch_reddit_mentions)
        if data is None:
            return None
        cache_set(cache_key, data)
    return data


@app.route("/api/professors/<slug>/full")
def professor_full(slug):
    data = professor_payload(slug)
    if data is None:
        return jsonify({"error": "Professor not found"}), 404
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "private, max-age=3600" if _is_authed() else "public, max-age=3600"
    resp.headers["Vary"] = "Authorization"
    return resp


@app.route("/api/departments")
def departments():
    college = request.args.get("college", "")
    cache_key = f"depts:{college or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)
    if college and college != "All":
        college_list = [c.strip() for c in college.split(",") if c.strip()]
        if len(college_list) == 1:
            rows = query("""
                SELECT DISTINCT department FROM professors_catalog
                WHERE avg_rating IS NOT NULL AND college = %s
                ORDER BY department
            """, (college_list[0],))
        else:
            rows = query("""
                SELECT DISTINCT department FROM professors_catalog
                WHERE avg_rating IS NOT NULL AND college IN (""" + ",".join(["%s"] * len(college_list)) + """)
                ORDER BY department
            """, tuple(college_list))
    else:
        rows = query("""
            SELECT DISTINCT department FROM professors_catalog
            WHERE avg_rating IS NOT NULL
            ORDER BY department
        """)
    BAD_DEPTS = {"Computer amp Informational Tech.", "Computer  Informational Tech.", "Counseling amp Educational Psych", "Counseling  Educational Psych"}
    result = [r['department'] for r in rows if r['department'] and r['department'] not in BAD_DEPTS]
    cache_set(cache_key, result)
    return jsonify(result)


def department_slug(name):
    """Deterministic, reversible department slug: lowercase, '&' -> 'and',
    any run of non-alphanumerics -> single '-', trim leading/trailing '-'."""
    s = name.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _department_hub_map():
    """slug -> {name, professorCount, avgRating} for every department with
    >=1 rated professor (same inclusion rule as professors_catalog), built
    from a single grouped query. Cached like the other catalog lookups.
    Collisions (two department names slugging to the same value) keep the
    first-seen entry and drop the rest."""
    cached = cache_get("dept_hub_map")
    if cached is not None:
        return cached
    rows = query("""
        SELECT department, COUNT(*) as cnt, AVG(avg_rating) as avg
        FROM professors_catalog
        WHERE avg_rating IS NOT NULL AND department IS NOT NULL AND department != ''
        GROUP BY department
    """)
    BAD_DEPTS = {"Computer amp Informational Tech.", "Computer  Informational Tech.", "Counseling amp Educational Psych", "Counseling  Educational Psych"}
    by_slug = {}
    for r in rows:
        name = r["department"]
        if not name or name in BAD_DEPTS:
            continue
        slug = department_slug(name)
        if slug in by_slug:
            continue  # collision: keep the first-seen department for this slug
        by_slug[slug] = {
            "slug": slug,
            "name": name,
            "professorCount": r["cnt"],
            "avgRating": round(r["avg"], 2) if r["avg"] is not None else None,
        }
    cache_set("dept_hub_map", by_slug)
    return by_slug


@app.route("/api/departments/hub")
def departments_hub():
    cache_key = "depts_hub_list"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)
    by_slug = _department_hub_map()
    entries = sorted(by_slug.values(), key=lambda d: d["professorCount"], reverse=True)
    result = {"departments": entries, "total": len(entries)}
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/departments/<slug>")
def department_hub_detail(slug):
    cache_key = f"dept_hub:{slug}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)
    by_slug = _department_hub_map()
    entry = by_slug.get(slug)
    if not entry:
        return jsonify({"error": "Department not found"}), 404

    rows = query("""
        SELECT name, slug, avg_rating, difficulty, would_take_again_pct, total_reviews
        FROM professors_catalog
        WHERE department = %s AND avg_rating IS NOT NULL
        ORDER BY avg_rating DESC
    """, (entry["name"],))
    professors = [{
        "name": r["name"],
        "slug": r["slug"],
        "avgRating": round(r["avg_rating"], 2) if r["avg_rating"] is not None else None,
        "difficulty": round(r["difficulty"], 2) if r["difficulty"] is not None else None,
        "wouldTakeAgainPct": round(r["would_take_again_pct"], 1) if r["would_take_again_pct"] is not None else None,
        "totalRatings": r["total_reviews"],
    } for r in rows]

    result = {
        "name": entry["name"],
        "slug": entry["slug"],
        "professorCount": entry["professorCount"],
        "avgRating": entry["avgRating"],
        "professors": professors,
    }
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/professors-catalog")
def professors_catalog():
    q = normalize_name(request.args.get("q", ""))
    college = request.args.get("college", "")
    dept = request.args.get("dept", "")
    sort = request.args.get("sort", "alpha")
    page = int(request.args.get("page", "1"))
    limit = min(int(request.args.get("limit", "20")), 10000)

    try:
        min_rating = float(request.args.get("minRating", "0"))
    except (ValueError, TypeError):
        min_rating = 0.0
    try:
        max_rating = float(request.args.get("maxRating", "5"))
    except (ValueError, TypeError):
        max_rating = 5.0
    try:
        min_reviews = int(request.args.get("minReviews", "1"))
    except (ValueError, TypeError):
        min_reviews = 1
    max_reviews_raw = request.args.get("maxReviews")
    try:
        max_reviews = int(max_reviews_raw) if max_reviews_raw is not None else None
    except (ValueError, TypeError):
        max_reviews = None

    cache_key = f"profcat:{q}:{college}:{dept}:{sort}:{page}:{limit}:{min_rating}:{max_rating}:{min_reviews}:{max_reviews}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    # If there's a search query, get matching name_keys first
    matched_name_keys = None
    if q and len(q) >= 2:
        matches = _professor_search(q, limit=200)
        matched_name_keys = [m["name_key"] for m in matches]
        if not matched_name_keys:
            return jsonify({"professors": [], "total": 0, "page": 1, "totalPages": 1})

    # Build dynamic query
    conditions = ["avg_rating IS NOT NULL"]
    params = []

    if college and college != "All":
        college_list = [c.strip() for c in college.split(",") if c.strip()]
        if len(college_list) == 1:
            conditions.append("college = %s")
            params.append(college_list[0])
        elif college_list:
            conditions.append("college IN (" + ",".join(["%s"] * len(college_list)) + ")")
            params.extend(college_list)
    DEPT_ALIASES = {
        "Computer & Informational Tech.": ["Computer amp Informational Tech.", "Computer  Informational Tech."],
        "Counseling & Educational Psych": ["Counseling amp Educational Psych", "Counseling  Educational Psych"],
    }
    if dept and dept != "All":
        dept_list = [d.strip() for d in dept.split(",") if d.strip()]
        expanded = []
        for d in dept_list:
            expanded.append(d)
            expanded.extend(DEPT_ALIASES.get(d, []))
        if len(expanded) == 1:
            conditions.append("department = %s")
            params.append(expanded[0])
        elif expanded:
            conditions.append("department IN (" + ",".join(["%s"] * len(expanded)) + ")")
            params.extend(expanded)
    if min_rating > 0:
        conditions.append("avg_rating >= %s")
        params.append(min_rating)
    if max_rating < 5:
        conditions.append("avg_rating <= %s")
        params.append(max_rating)
    if min_reviews > 1:
        conditions.append("total_reviews >= %s")
        params.append(min_reviews)
    if max_reviews is not None:
        conditions.append("total_reviews <= %s")
        params.append(max_reviews)

    where = " AND ".join(conditions)

    if matched_name_keys is not None:
        # Filter to search matches, preserve search order
        placeholders = ",".join(["%s"] * len(matched_name_keys))
        # Get total count
        count_row = query_one(
            f"SELECT COUNT(*) as cnt FROM professors_catalog WHERE {where} AND name_key IN ({placeholders})",
            params + matched_name_keys
        )
        total = count_row["cnt"]

        # Get page data - preserve search ranking order
        total_pages = max(1, (total + limit - 1) // limit)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * limit

        all_rows = query(
            f"SELECT * FROM professors_catalog WHERE {where} AND name_key IN ({placeholders})",
            params + matched_name_keys
        )

        # Reorder by search ranking
        name_key_order = {nk: i for i, nk in enumerate(matched_name_keys)}
        all_rows.sort(key=lambda r: name_key_order.get(r["name_key"], 999999))
        page_rows = all_rows[offset:offset + limit]
    else:
        # No search - use SQL sorting
        if sort == "rating":
            order = "avg_rating DESC NULLS LAST"
        elif sort == "comments":
            order = "total_comments DESC"
        else:
            order = "lower(name) ASC"

        count_row = query_one(f"SELECT COUNT(*) as cnt FROM professors_catalog WHERE {where}", params)
        total = count_row["cnt"]

        total_pages = max(1, (total + limit - 1) // limit)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * limit

        page_rows = query(
            f"SELECT * FROM professors_catalog WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s",
            params + [limit, offset]
        )

    professors = []
    for row in page_rows:
        professors.append({
            "name": row["name"],
            "slug": row["slug"],
            "department": row["department"],
            "college": row["college"],
            "avgRating": round(row["avg_rating"], 2) if row["avg_rating"] else None,
            "rmpRating": round(row["rmp_rating"], 2) if row["rmp_rating"] else None,
            "totalReviews": row["total_reviews"],
            "totalComments": row.get("total_comments", 0) or 0,
            "wouldTakeAgainPct": round(row["would_take_again_pct"], 1) if row["would_take_again_pct"] else None,
            "imageUrl": row["image_url"],
            "focusX": row.get("focus_x") if row.get("focus_x") is not None else 50.0,
            "focusY": row.get("focus_y") if row.get("focus_y") is not None else 30.0,
        })

    result = {
        "professors": professors,
        "total": total,
        "page": page,
        "totalPages": total_pages,
    }
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/course-departments")
def course_departments():
    cached = cache_get("course_depts")
    if cached:
        return jsonify(cached)
    rows = query("""
        SELECT DISTINCT department FROM course_catalog
        WHERE department IS NOT NULL AND department != ''
        ORDER BY department
    """)
    result = [r["department"] for r in rows]
    cache_set("course_depts", result)
    return jsonify(result)


@app.route("/api/courses-catalog")
def courses_catalog():
    q = normalize_name(request.args.get("q", ""))
    dept = request.args.get("dept", "")
    sort = request.args.get("sort", "alpha")
    page = int(request.args.get("page", "1"))
    limit = min(int(request.args.get("limit", "20")), 10000)

    try:
        min_rating = float(request.args.get("minRating", "0"))
    except (ValueError, TypeError):
        min_rating = 0.0

    try:
        max_rating = float(request.args.get("maxRating", "5"))
    except (ValueError, TypeError):
        max_rating = 5.0

    cache_key = f"coursecat:{q}:{dept}:{sort}:{page}:{limit}:{min_rating}:{max_rating}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    # avg_rating is precomputed on course_catalog (see precompute.py step 8),
    # so this is a plain indexed SELECT/WHERE/ORDER/LIMIT like professors_catalog.
    conditions = []
    params = []

    if dept and dept != "All":
        dept_list = [d.strip() for d in dept.split(",") if d.strip()]
        if len(dept_list) == 1:
            conditions.append("department = %s")
            params.append(dept_list[0])
        elif dept_list:
            conditions.append("department IN (" + ",".join(["%s"] * len(dept_list)) + ")")
            params.extend(dept_list)
    if q:
        conditions.append("search_text LIKE %s")
        params.append(f"%{q}%")
    if min_rating > 0:
        conditions.append("avg_rating >= %s")
        params.append(min_rating)
    if max_rating < 5:
        conditions.append("avg_rating <= %s")
        params.append(max_rating)

    where_str = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if sort == "rating":
        order = "avg_rating DESC NULLS LAST"
    else:
        order = "lower(code) ASC"

    count_row = query_one(f"SELECT COUNT(*) as cnt FROM course_catalog {where_str}", params)
    total = count_row["cnt"] if count_row else 0

    total_pages = max(1, (total + limit - 1) // limit)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * limit

    rows = query(f"""
        SELECT code, name, department, avg_rating FROM course_catalog
        {where_str}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """, params + [limit, offset])

    courses = [
        {
            "code": r["code"],
            "name": r["name"],
            "department": r["department"],
            "avgRating": round(r["avg_rating"], 2) if r["avg_rating"] is not None else None,
        }
        for r in rows
    ]

    result = {
        "courses": courses,
        "total": total,
        "page": page,
        "totalPages": total_pages,
    }
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/courses/<code>")
def course_profile(code):
    code_norm = _format_course_code(code)
    if not code_norm:
        return jsonify({"error": "Course not found"}), 404

    is_authed = False
    token = _get_auth_token()
    if token:
        try:
            pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            is_authed = True
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
            pass

    cache_key = f"course:{code_norm}:{'a' if is_authed else 'u'}"
    cached = cache_get(cache_key)
    if cached:
        resp = jsonify(cached)
        resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
        resp.headers["Vary"] = "Authorization"
        return resp

    # Look up course in catalog
    # SELECT * so a catalog built before is_topics existed still serves (the
    # column reads as absent, i.e. not a topics code).
    course = query_one("SELECT * FROM course_catalog WHERE code = %s", (code_norm,))
    if not course:
        return jsonify({"error": "Course not found"}), 404

    # Get all sections for this course from trace_courses using indexed course_code column
    sections = query("""
        SELECT DISTINCT ON (tc.course_id, tc.instructor_id, tc.term_id)
            tc.course_id, tc.instructor_id, tc.term_id, tc.term_title,
            tc.department_name, tc.display_name, tc.section, tc.enrollment,
            tc.instructor_first_name, tc.instructor_last_name
        FROM trace_courses tc
        WHERE tc.course_code = %s
        ORDER BY tc.course_id, tc.instructor_id, tc.term_id, tc.term_id DESC
    """, (code_norm,))

    if not sections:
        return jsonify({"error": "Course not found"}), 404

    # Single query for all score types using conditional aggregation (replaces 3 separate queries)
    section_keys = tuple((s["course_id"], s["instructor_id"], s["term_id"]) for s in sections)
    combined_scores = query(
        "SELECT course_id, instructor_id, term_id, "
        "SUM(CASE WHEN lower(question) LIKE '%%overall%%' AND lower(question) != 'overall effectiveness' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) as overall_weighted, "
        "SUM(CASE WHEN lower(question) LIKE '%%overall%%' AND lower(question) != 'overall effectiveness' THEN CAST(total_responses AS INT) ELSE 0 END) as overall_responses, "
        "SUM(CASE WHEN lower(question) LIKE '%%overall%%' AND lower(question) != 'overall effectiveness' THEN completed ELSE 0 END) as overall_completed, "
        "SUM(CASE WHEN lower(question) LIKE '%%challeng%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) as challeng_weighted, "
        "SUM(CASE WHEN lower(question) LIKE '%%challeng%%' THEN CAST(total_responses AS INT) ELSE 0 END) as challeng_responses, "
        "SUM(CASE WHEN lower(question) LIKE '%%hours%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) as hours_weighted, "
        "SUM(CASE WHEN lower(question) LIKE '%%hours%%' THEN CAST(total_responses AS INT) ELSE 0 END) as hours_responses "
        "FROM trace_scores "
        "WHERE (course_id, instructor_id, term_id) IN %s "
        "AND ((lower(question) LIKE '%%overall%%' AND lower(question) != 'overall effectiveness') OR lower(question) LIKE '%%challeng%%' OR lower(question) LIKE '%%hours%%') "
        "GROUP BY course_id, instructor_id, term_id",
        (section_keys,)
    )

    # Build score maps from combined result
    score_map = {}
    challenging_map = {}
    hours_map = {}
    for row in combined_scores:
        key = (row["course_id"], row["instructor_id"], row["term_id"])
        if row["overall_responses"]:
            score_map[key] = {
                "weighted_sum": row["overall_weighted"],
                "total_responses": row["overall_responses"],
                "completed": row["overall_completed"],
            }
        if row["challeng_responses"]:
            challenging_map[key] = {
                "weighted_sum": row["challeng_weighted"],
                "total_responses": row["challeng_responses"],
            }
        if row["hours_responses"]:
            hours_map[key] = {
                "weighted_sum": row["hours_weighted"],
                "total_responses": row["hours_responses"],
            }

    # Compute summary
    total_weighted = 0.0
    total_responses = 0
    total_enrollment = 0
    total_sections_with_enrollment = 0
    latest_term_id = 0
    latest_term_title = ""
    latest_term_sort = -1

    for s in sections:
        enrollment = _safe_int(s["enrollment"])
        if enrollment > 0:
            total_enrollment += enrollment
            total_sections_with_enrollment += 1
        tid = _safe_int(s["term_id"])
        tsort = term_sort_key(s["term_title"] or "")
        if tsort > latest_term_sort:
            latest_term_sort = tsort
            latest_term_id = tid
            latest_term_title = s["term_title"] or ""
        key = (s["course_id"], s["instructor_id"], s["term_id"])
        if key in score_map:
            total_weighted += _safe_float(score_map[key]["weighted_sum"])
            total_responses += _safe_int(score_map[key]["total_responses"])

    avg_rating = (total_weighted / total_responses) if total_responses > 0 else None

    # A topics code (e.g. HONR3310 running as "Election 2024" and "Language and
    # Power" in the same term) is a container for unrelated classes, so a single
    # course-level average would blend them. Its sections keep their own ratings.
    is_topics = bool(course.get("is_topics"))

    summary = {
        "code": course["code"],
        "name": course["name"],
        "department": course["department"] or "",
        "isTopics": is_topics,
        "avgRating": round(avg_rating, 2) if avg_rating is not None and not is_topics else None,
        "avgEnrollment": round(total_enrollment / total_sections_with_enrollment) if total_sections_with_enrollment > 0 else None,
        "latestTermTitle": latest_term_title,
        # Count of TRACE "overall" question responses backing avgRating, for
        # AggregateRating JSON-LD (schema.org requires ratingCount alongside ratingValue).
        "ratingCount": total_responses if total_responses > 0 and not is_topics else None,
    }

    # Build instructor aggregates
    instructor_data = {}
    for s in sections:
        fname = (s["instructor_first_name"] or "").strip()
        lname = (s["instructor_last_name"] or "").strip()
        name = f"{fname} {lname}".strip()
        if not name:
            continue
        if name not in instructor_data:
            instructor_data[name] = {
                "sections": 0, "enrollment": 0,
                "weighted": 0.0, "responses": 0,
                "challeng_weighted": 0.0, "challeng_responses": 0,
                "hours_weighted": 0.0, "hours_responses": 0,
                "latest_term_title": "", "latest_term_sort": -1,
            }
        tsort = term_sort_key(s["term_title"] or "")
        if tsort > instructor_data[name]["latest_term_sort"]:
            instructor_data[name]["latest_term_sort"] = tsort
            instructor_data[name]["latest_term_title"] = s["term_title"] or ""
        instructor_data[name]["sections"] += 1
        instructor_data[name]["enrollment"] += _safe_int(s["enrollment"])
        key = (s["course_id"], s["instructor_id"], s["term_id"])
        if key in score_map:
            instructor_data[name]["weighted"] += _safe_float(score_map[key]["weighted_sum"])
            instructor_data[name]["responses"] += _safe_int(score_map[key]["total_responses"])
        if key in challenging_map:
            instructor_data[name]["challeng_weighted"] += _safe_float(challenging_map[key]["weighted_sum"])
            instructor_data[name]["challeng_responses"] += _safe_int(challenging_map[key]["total_responses"])
        if key in hours_map:
            instructor_data[name]["hours_weighted"] += _safe_float(hours_map[key]["weighted_sum"])
            instructor_data[name]["hours_responses"] += _safe_int(hours_map[key]["total_responses"])

    # Look up instructor metadata from professors_catalog (batched)
    name_key_map = {normalize_name(name): name for name in instructor_data}
    name_keys = list(name_key_map.keys())
    prof_map = {}
    comment_counts = {}
    rmp_course_diff_map = {}
    rmp_key_of = {}
    if name_keys:
        placeholders = ",".join(["%s"] * len(name_keys))
        prof_rows = query(
            f"SELECT name_key, slug, image_url, total_reviews, would_take_again_pct, difficulty, rmp_rating "
            f"FROM professors_catalog WHERE name_key IN ({placeholders})", name_keys
        )
        prof_map = {r["name_key"]: r for r in prof_rows}
        # These instructor names are TRACE's spelling. A fuzzy-matched professor's
        # catalog row is keyed by the RMP spelling and records TRACE's in
        # trace_name_key, so without this second lookup their card here has no
        # profile link, photo or review counts. An exact name_key match wins.
        for r in catalog_rows_by_trace_keys([k for k in name_keys if k not in prof_map]):
            prof_map.setdefault(r["trace_name_key"], r)
        # RMP rows (difficulty, comments) are stored under the catalog's RMP key.
        rmp_key_of = {nk: (prof_map[nk]["name_key"] if nk in prof_map else nk)
                      for nk in name_keys}
        rmp_keys = list(set(rmp_key_of.values()))
        rmp_placeholders = ",".join(["%s"] * len(rmp_keys))
        # Fuzzy match RMP course: exact normalized match, or match on numeric portion
        # (RMP course names are often misspelled, e.g. "C1100" instead of "CS1100")
        code_num = re.sub(r"[^0-9]", "", code_norm)
        rmp_course_diff_rows = query(
            f"SELECT name_key, AVG(CAST(difficulty AS FLOAT)) as avg_diff "
            f"FROM rmp_reviews "
            f"WHERE name_key IN ({rmp_placeholders}) AND difficulty IS NOT NULL "
            f"AND (UPPER(REPLACE(course, ' ', '')) = %s OR REGEXP_REPLACE(course, '[^0-9]', '', 'g') = %s) "
            f"GROUP BY name_key",
            rmp_keys + [code_norm, code_num]
        )
        rmp_course_diff_map = {r["name_key"]: round(float(r["avg_diff"]), 2) for r in rmp_course_diff_rows if r["avg_diff"] is not None}
        # Each side counted under its own key, then added per instructor: RMP
        # comments live under the RMP spelling, TRACE comments under TRACE's.
        rmp_counts = {r["name_key"]: int(r["cnt"]) for r in query(
            f"SELECT name_key, COUNT(*) as cnt FROM rmp_reviews "
            f"WHERE name_key IN ({rmp_placeholders}) AND comment IS NOT NULL AND comment != '' "
            f"GROUP BY name_key",
            rmp_keys
        )}
        trace_counts = {r["name_key"]: int(r["cnt"]) for r in query(
            f"SELECT tc2.name_key, COUNT(*) as cnt "
            f"FROM trace_comments tc "
            f"JOIN trace_courses tc2 ON tc.tc_course_id = tc2.course_id "
            f"  AND tc.tc_instructor_id = tc2.instructor_id "
            f"  AND tc.tc_term_id = tc2.term_id "
            f"WHERE tc2.name_key IN ({placeholders}) "
            f"AND tc.comment IS NOT NULL AND tc.comment != '' "
            f"GROUP BY tc2.name_key",
            name_keys
        )}
        for nk in name_keys:
            comment_counts[nk] = rmp_counts.get(rmp_key_of[nk], 0) + trace_counts.get(nk, 0)

    instructor_rows = []
    for name, data in instructor_data.items():
        prof = prof_map.get(normalize_name(name))
        nk = normalize_name(name)
        meta_slug = prof["slug"] if prof else ""
        meta_image = prof["image_url"] if prof else None
        meta_reviews = prof["total_reviews"] if prof else 0
        meta_wta = round(prof["would_take_again_pct"], 1) if prof and prof["would_take_again_pct"] else None
        meta_diff = round(prof["difficulty"], 2) if prof and prof["difficulty"] else None
        meta_comments = comment_counts.get(nk, 0)

        resp = data["responses"]
        challeng_resp = data["challeng_responses"]
        hours_resp = data["hours_responses"]
        trace_diff = round(data["challeng_weighted"] / challeng_resp, 2) if challeng_resp > 0 else None
        rmp_course_diff = rmp_course_diff_map.get(rmp_key_of.get(nk, nk))
        if trace_diff is not None and rmp_course_diff is not None:
            course_diff = round((trace_diff + rmp_course_diff) / 2, 2)
        elif trace_diff is not None:
            course_diff = trace_diff
        else:
            course_diff = rmp_course_diff
        instructor_rows.append({
            "name": name,
            "slug": meta_slug,
            "imageUrl": meta_image,
            "difficulty": meta_diff,
            "wouldTakeAgainPct": meta_wta,
            "totalReviews": meta_reviews or 0,
            "totalComments": meta_comments,
            "_sections": data["sections"],
            "latestTermTitle": data["latest_term_title"],
            "avgRating": round(data["weighted"] / resp, 2) if resp > 0 else None,
            "courseAvgDifficulty": course_diff,
            "courseAvgHoursPerWeek": round(data["hours_weighted"] / hours_resp, 2) if hours_resp > 0 else None,
        })
    instructor_rows.sort(key=lambda r: (r["avgRating"] is None, -(r["avgRating"] or 0), -r["_sections"]))
    for row in instructor_rows:
        del row["_sections"]

    # Build section rows
    section_rows = []
    for s in sorted(sections, key=lambda x: -(x["term_id"] or 0)):
        key = (s["course_id"], s["instructor_id"], s["term_id"])
        sc = score_map.get(key)
        fname = (s["instructor_first_name"] or "").strip()
        lname = (s["instructor_last_name"] or "").strip()
        name = f"{fname} {lname}".strip()
        overall_mean = None
        if sc and _safe_int(sc["total_responses"]) > 0:
            overall_mean = round(_safe_float(sc["weighted_sum"]) / _safe_int(sc["total_responses"]), 2)
        prof = prof_map.get(normalize_name(name))
        rmp_rating = round(prof["rmp_rating"], 2) if prof and prof.get("rmp_rating") else None
        section_rows.append({
            "termId": _safe_int(s["term_id"]),
            "termTitle": s["term_title"] or "",
            "instructor": name,
            "overallRating": overall_mean if is_authed else None,
            "rmpRating": rmp_rating if is_authed else None,
        })

    # Get question-level scores
    question_rows = []
    q_scores = query(
        "SELECT question, "
        "SUM(CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT)) as weighted_sum, "
        "SUM(total_responses) as total_responses "
        "FROM trace_scores "
        "WHERE (course_id, instructor_id, term_id) IN %s "
        "GROUP BY question",
        (section_keys,)
    )
    for qs in q_scores:
        resp = _safe_int(qs["total_responses"])
        question_rows.append({
            "question": qs["question"],
            "avgRating": round(_safe_float(qs["weighted_sum"]) / resp, 2) if resp > 0 else None,
            "_totalResponses": resp,
        })
    question_rows.sort(key=lambda r: (-r["_totalResponses"], r["question"].lower()))
    for row in question_rows:
        del row["_totalResponses"]

    result = {
        "summary": summary,
        "instructors": instructor_rows,
        "sections": section_rows if is_authed else [],
        "questionScores": question_rows if is_authed else [],
    }
    cache_set(cache_key, result)
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
    resp.headers["Vary"] = "Authorization"
    return resp


# ──────────────────────────────────────────────
#  Google OAuth routes
# ──────────────────────────────────────────────
def _get_redirect_uri():
    # Use FRONTEND_URL so OAuth callbacks go through the Vercel proxy, not direct to Railway
    return f"{FRONTEND_URL}/api/auth/google/callback"


@app.route("/api/auth/google")
@limiter.limit("10 per minute")
def auth_google():
    return_to = request.args.get("returnTo", "/")
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _get_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "hd": "husky.neu.edu",
    }
    is_popup = request.args.get("popup") == "1"
    resp = make_response(redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}"))
    resp.set_cookie("auth_return_to", return_to, max_age=600, httponly=True, samesite="None", secure=True)
    if is_popup:
        resp.set_cookie("auth_popup", "1", max_age=600, httponly=True, samesite="None", secure=True)
    return resp


@app.route("/api/auth/google/callback")
@limiter.limit("10 per minute")
def auth_google_callback():
    code = request.args.get("code")
    if not code:
        return redirect(f"{FRONTEND_URL}?auth_error=no_code")

    try:
        token_resp = http_requests.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": _get_redirect_uri(),
            "grant_type": "authorization_code",
        }, timeout=10)
        if token_resp.status_code != 200:
            return redirect(f"{FRONTEND_URL}?auth_error=token_exchange_failed")

        access_token = token_resp.json().get("access_token")

        user_resp = http_requests.get(GOOGLE_USERINFO_URL, headers={
            "Authorization": f"Bearer {access_token}",
        }, timeout=10)
        if user_resp.status_code != 200:
            return redirect(f"{FRONTEND_URL}?auth_error=userinfo_failed")
    except Exception:
        return redirect(f"{FRONTEND_URL}?auth_error=timeout")

    user_info = user_resp.json()

    if user_info.get("hd") != "husky.neu.edu":
        return redirect(f"{FRONTEND_URL}?auth_error=invalid_domain")

    payload = {
        "sub": user_info["id"],
        "email": user_info["email"],
        "name": user_info.get("name", ""),
        "picture": user_info.get("picture", ""),
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    token = pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")

    is_popup = request.cookies.get("auth_popup") == "1"

    if is_popup:
        # Desktop popup: post message to opener and close
        resp = make_response(f"""
        <html><body><script>
          window.opener && window.opener.postMessage({{ type: "auth_complete", token: "{token}" }}, "{FRONTEND_URL}");
          window.close();
        </script></body></html>
        """)
        resp.delete_cookie("auth_popup")
    else:
        # Mobile redirect: pass token via URL fragment (not querystring, so it's not logged)
        return_to = request.cookies.get("auth_return_to", "/")
        from urllib.parse import urlparse
        parsed = urlparse(return_to)
        if parsed.scheme or parsed.netloc or not return_to.startswith("/"):
            return_to = "/"
        resp = make_response(redirect(f"{FRONTEND_URL}{return_to}#auth_token={token}"))
        resp.delete_cookie("auth_return_to")
    return resp


def _get_auth_token():
    """Get JWT token from Authorization header or cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.cookies.get("auth_token")


def _is_authed():
    """True when the request carries a valid session token. Pages serve the same
    payload either way; only the Cache-Control header differs."""
    token = _get_auth_token()
    if not token:
        return False
    try:
        pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return True
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
        return False


@app.route("/api/auth/me")
@limiter.limit("30 per minute")
def auth_me():
    token = _get_auth_token()
    if not token:
        return jsonify(None), 401

    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return jsonify({
            "email": payload["email"],
            "name": payload["name"],
            "picture": payload.get("picture", ""),
        })
    except pyjwt.ExpiredSignatureError:
        return jsonify(None), 401
    except pyjwt.InvalidTokenError:
        return jsonify(None), 401


@app.route("/api/auth/logout", methods=["POST"])
@limiter.limit("10 per minute")
def auth_logout():
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("auth_token")
    return resp


def _require_bookmarks_auth():
    """Hard-401 auth gate shared by all three /api/bookmarks routes (same
    pattern as /api/chat's question mode). Returns the JWT `sub` claim, or
    None if a 401 JSON response has already been returned in its place."""
    token = _get_auth_token()
    if not token:
        return None
    try:
        claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
        return None
    return claims["sub"]


@app.route("/api/bookmarks")
@limiter.limit("30 per minute")
def bookmarks_get():
    user_sub = _require_bookmarks_auth()
    if not user_sub:
        return jsonify({"error": "Sign in required"}), 401
    return jsonify(bookmarks.list_bookmarks(user_sub, query))


@app.route("/api/bookmarks", methods=["POST"])
@limiter.limit("20 per minute")
def bookmarks_add():
    user_sub = _require_bookmarks_auth()
    if not user_sub:
        return jsonify({"error": "Sign in required"}), 401

    data = request.get_json(silent=True) or {}
    item_type = (data.get("itemType") or "").strip()
    item_key = (data.get("itemKey") or "").strip()
    if item_type not in ("professor", "course") or not item_key:
        return jsonify({"error": "itemType must be 'professor' or 'course', itemKey is required"}), 400

    status = bookmarks.add_bookmark(user_sub, item_type, item_key, query_one, _write)
    if status == "not_found":
        return jsonify({"error": "Not found"}), 404
    if status == "limit_reached":
        return jsonify({"error": "Bookmark limit reached"}), 400
    return jsonify({"ok": True})


@app.route("/api/bookmarks", methods=["DELETE"])
@limiter.limit("20 per minute")
def bookmarks_remove():
    user_sub = _require_bookmarks_auth()
    if not user_sub:
        return jsonify({"error": "Sign in required"}), 401

    data = request.get_json(silent=True) or {}
    item_type = (data.get("itemType") or "").strip()
    item_key = (data.get("itemKey") or "").strip()
    if item_type not in ("professor", "course") or not item_key:
        return jsonify({"error": "itemType must be 'professor' or 'course', itemKey is required"}), 400

    bookmarks.remove_bookmark(user_sub, item_type, item_key, _write)
    return jsonify({"ok": True})


@app.route("/api/feedback", methods=["POST"])
@limiter.limit("10 per day")
def submit_feedback():
    global _feedback_count, _feedback_date

    data = request.get_json(silent=True) or {}
    feedback_type = data.get("feedbackType", "").strip()
    description = data.get("description", "").strip()
    reply_email = data.get("email", "").strip()
    turnstile_token = data.get("turnstileToken", "").strip()
    account_token = (data.get("accountSub") or "").strip()

    # Verify Cloudflare Turnstile CAPTCHA
    turnstile_secret = os.getenv("TURNSTILE_SECRET_KEY")
    if turnstile_secret:
        if not turnstile_token:
            return jsonify({"error": "CAPTCHA verification required"}), 400
        try:
            verify_resp = http_requests.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": turnstile_secret, "response": turnstile_token, "remoteip": get_real_ip()},
                timeout=5,
            )
            if not verify_resp.ok or not verify_resp.json().get("success"):
                return jsonify({"error": "CAPTCHA verification failed"}), 403
        except Exception:
            return jsonify({"error": "CAPTCHA verification failed"}), 500

    if not feedback_type or not description:
        return jsonify({"error": "feedbackType and description are required"}), 400

    # Ask ban appeals and data-deletion requests are useless without a reply address — require
    # it (other types stay optional).
    if feedback_type in _ACCOUNT_FEEDBACK_TYPES and not reply_email:
        return jsonify({"error": "Email is required for this request"}), 400

    if reply_email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', reply_email):
        return jsonify({"error": "Invalid email address"}), 400

    # Resolve the account from its JWT (verified server-side, never trust a client-supplied id)
    # so we can locate/clear its ask_log rows by session_token (appeal review or data erasure).
    appeal_account = None
    if feedback_type in _ACCOUNT_FEEDBACK_TYPES and account_token:
        try:
            appeal_account = pyjwt.decode(account_token, JWT_SECRET, algorithms=["HS256"]).get("sub")
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
            appeal_account = None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _feedback_lock:
        if _feedback_date != today:
            _feedback_date = today
            _feedback_count = 0
        if _feedback_count >= FEEDBACK_DAILY_LIMIT:
            return jsonify({"error": "Daily feedback limit reached. Please try again tomorrow."}), 429
        _feedback_count += 1

    resend_api_key = os.getenv("RESEND_API_KEY")
    if not resend_api_key:
        print("[feedback] RESEND_API_KEY not configured")
        return jsonify({"error": "Email service not configured"}), 500

    type_labels = {
        "bug": "Bug Report",
        "feature": "Feature Request",
        "missing": "Missing Data",
        "incorrectdata": "Incorrect Data",
        "banappeal": "Ask Ban Appeal",
        "datadeletion": "Data Deletion Request",
        "general": "General Feedback",
    }
    type_label = type_labels.get(feedback_type, feedback_type)
    submitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        f"Submitted:   {submitted_at}",
        f"Type:        {type_label}",
    ]
    if reply_email:
        lines.append(f"From:        {reply_email}")
    if feedback_type in _ACCOUNT_FEEDBACK_TYPES:
        # surface the session_token to act on via clear_ask_strikes.py
        # (--account <sub> for appeals, --purge-account <sub> for data deletion)
        lines.append(f"Account:     {appeal_account or 'not signed in / token invalid'}")
    lines += ["", "Description:", description]
    body = "\n".join(lines)

    payload = {
        "from": "RateMyHusky <feedback@ratemyhusky.com>",
        "to": ["feedback@ratemyhusky.com"],
        "subject": f"[RateMyHusky] {type_label}",
        "text": body,
    }
    if reply_email:
        payload["reply_to"] = reply_email

    try:
        resp = http_requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        if not resp.ok:
            print(f"[feedback] Resend error {resp.status_code}: {resp.text}")
            return jsonify({"error": "Failed to send email"}), 500
    except Exception as e:
        print(f"[feedback] Resend request error: {e}")
        return jsonify({"error": "Failed to send email"}), 500

    return jsonify({"ok": True})


from render import render_bp
app.register_blueprint(render_bp)

if __name__ == "__main__":
    print("Starting server on port 5001...")
    app.run(debug=True, port=5001, use_reloader=True)
