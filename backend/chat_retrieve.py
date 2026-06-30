import sys, re, argparse

# A course code looks like 2–5 letters then 4 digits, optionally space-separated
# (e.g. "DS3000", "CS 3000", "ENGW1111"). Used to route a gate hint to the course path.
_COURSE_CODE_RE = re.compile(r"^[A-Za-z]{2,5}\s?\d{4}$")

def _norm_course_code(text):
    return re.sub(r"\s+", "", str(text or "").upper())

def is_course_code(text):
    return bool(text) and bool(_COURSE_CODE_RE.match(str(text).strip()))

# Topic-listing questions name a subject, not a specific course/professor:
# "what database courses are there", "courses about machine learning".
_TOPIC_PATTERNS = [
    re.compile(r"(?:what|which|are there any|any|list|show me)\s+(?:are\s+)?(?:the\s+)?(.+?)\s+(?:courses|classes|electives)\b", re.I),
    re.compile(r"(?:courses|classes|electives)\s+(?:about|on|in|for|related to)\s+(.+)", re.I),
]
_TOPIC_STOPWORDS = {"a", "an", "the", "any", "some", "these", "those", "all"}
_TOPIC_RATING_ADJECTIVES = {"best", "top", "highest", "good", "great", "easiest", "hardest", "easy", "hard", "worst", "lowest"}
_RATINGS_RE = re.compile(r"\b(rated|rating|ratings|best|top|highest|good|easiest|hardest|which is)\b", re.I)

def is_course_topic_query(text):
    """Return the lowercased topic when the text asks to LIST courses by subject, else None.
    Rejects topics that are actually course codes (so 'what is CS3500' isn't hijacked)."""
    t = (text or "").strip()
    for pat in _TOPIC_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        topic = m.group(1).strip().strip("?.! ").lower()
        toks = topic.split()
        while toks and (toks[0] in _TOPIC_STOPWORDS or toks[0] in _TOPIC_RATING_ADJECTIVES):
            toks = toks[1:]
        topic = " ".join(toks)
        if topic and not is_course_code(topic):
            return topic
    return None

def wants_ratings(text):
    """True when the listing question also asks about quality/ratings."""
    return bool(_RATINGS_RE.search(text or ""))

def _course_overall_rating(code, query_fn):
    """Weighted overall TRACE rating for one course code (same pattern as fetch_course_facts)."""
    rows = query_fn("""
        SELECT
          SUM(CASE WHEN lower(ts.question) LIKE '%%overall%%' THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS o_w,
          SUM(CASE WHEN lower(ts.question) LIKE '%%overall%%' THEN CAST(ts.total_responses AS INT) ELSE 0 END) AS o_r
        FROM trace_scores ts
        JOIN trace_courses tc ON ts.course_id = tc.course_id
          AND ts.instructor_id = tc.instructor_id AND ts.term_id = tc.term_id
        WHERE tc.course_code = %s
    """, (_norm_course_code(code),))
    if not rows:
        return None
    w = float(rows[0].get("o_w") or 0); r = float(rows[0].get("o_r") or 0)
    return round(w / r, 2) if r > 0 else None

def fetch_courses_by_topic(topic, query_fn, limit=8, with_ratings=False):
    """Catalog courses whose search_text matches the topic. Reuses the /api/search course
    query. Per-course rating lookups happen ONLY when with_ratings is True."""
    like = f"%{topic}%"
    rows = query_fn("""
        SELECT code, name, department FROM course_catalog
        WHERE search_text LIKE %s
        ORDER BY CASE WHEN lower(code) LIKE %s THEN 0 ELSE 1 END, code
        LIMIT %s
    """, (like, f"{topic}%", limit))
    courses = [{"code": r["code"], "name": r["name"], "department": r.get("department")} for r in rows]
    if with_ratings:
        for c in courses:
            c["rating"] = _course_overall_rating(c["code"], query_fn)
        courses.sort(key=lambda c: (c.get("rating") is not None, c.get("rating") or 0), reverse=True)
    return courses

def _clean_course_label(display_name):
    """trace_courses.display_name looks like 'ENGW3302:09 (Advanced Writing in Tech Prof)
    - Laurie Nardone'. For "courses taught" we want just the code + course name, with no
    section number, term, or instructor — so the same course collapses to one entry."""
    dn = str(display_name or "").strip()
    if not dn:
        return ""
    code = re.split(r"[:\s]", dn, 1)[0].strip().rstrip(":")
    m = re.search(r"\(([^)]*)\)", dn)  # course name lives inside the first ( )
    name = (m.group(1) if m else "").strip()
    return f"{code} {name}".strip() if name else code

def resolve_entity(query, hint, prof_search_fn, limit=1):
    for term in (hint, query):
        if not term:
            continue
        rows = prof_search_fn(term, limit=limit)
        if rows:
            return rows[0]
    return None

def fetch_facts(slug, query_one_fn, query_fn):
    prof = query_one_fn("""
        SELECT slug, name_key, name, department, rmp_rating, trace_rating, avg_rating,
               difficulty, would_take_again_pct, total_reviews, avg_hours
        FROM professors_catalog WHERE slug = %s
    """, (slug,))
    if not prof:
        return {}
    name_key = prof.get("name_key")
    course_rows = query_fn("""
        SELECT DISTINCT display_name FROM trace_courses
        WHERE name_key = %s AND display_name IS NOT NULL
        ORDER BY display_name LIMIT 25
    """, (name_key,))
    seen, courses = set(), []
    for c in course_rows:
        label = _clean_course_label(c.get("display_name"))
        if label and label not in seen:
            seen.add(label); courses.append(label)
    # total written comments = RMP review comments + TRACE comments (same buckets the
    # professor page counts), so Ask reports the same number the profile shows.
    cc = query_one_fn("""
        SELECT COALESCE(SUM(cnt), 0) AS cnt FROM (
          SELECT COUNT(*) AS cnt FROM rmp_reviews
            WHERE name_key = %s AND comment IS NOT NULL AND comment != ''
          UNION ALL
          SELECT COUNT(*) AS cnt FROM trace_comments tc
            JOIN trace_courses tc2 ON tc.tc_course_id = tc2.course_id
              AND tc.tc_instructor_id = tc2.instructor_id AND tc.tc_term_id = tc2.term_id
            WHERE tc2.name_key = %s AND tc.comment IS NOT NULL AND tc.comment != ''
        ) sub
    """, (name_key, name_key))
    return {
        "kind": "professor",
        "name": prof.get("name"), "department": prof.get("department"),
        "rmp_rating": prof.get("rmp_rating"), "trace_rating": prof.get("trace_rating"),
        "avg_rating": prof.get("avg_rating"), "difficulty": prof.get("difficulty"),
        "would_take_again_pct": prof.get("would_take_again_pct"),
        "total_reviews": prof.get("total_reviews"),
        "hours_per_week": prof.get("avg_hours"),
        "total_comments": (cc or {}).get("cnt", 0),
        "courses": courses,
    }

def fetch_course_facts(code, query_one_fn, query_fn):
    """Compact course summary for Ask: overall rating, avg difficulty (challenge), avg
    hrs/week, last-taught term, and recent professor names. Reuses the trace_scores
    weighted-aggregation pattern from /api/courses/<code>, aggregated at course grain."""
    norm = _norm_course_code(code)
    cat = query_one_fn(
        "SELECT code, name, department FROM course_catalog WHERE code = %s", (norm,))
    if not cat:
        return {}
    agg = query_one_fn("""
        SELECT
          SUM(CASE WHEN lower(question) LIKE '%%overall%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) AS o_w,
          SUM(CASE WHEN lower(question) LIKE '%%overall%%' THEN CAST(total_responses AS INT) ELSE 0 END) AS o_r,
          SUM(CASE WHEN lower(question) LIKE '%%challeng%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) AS c_w,
          SUM(CASE WHEN lower(question) LIKE '%%challeng%%' THEN CAST(total_responses AS INT) ELSE 0 END) AS c_r,
          SUM(CASE WHEN lower(question) LIKE '%%hours%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) AS h_w,
          SUM(CASE WHEN lower(question) LIKE '%%hours%%' THEN CAST(total_responses AS INT) ELSE 0 END) AS h_r
        FROM trace_scores ts
        JOIN trace_courses tc ON ts.course_id = tc.course_id
          AND ts.instructor_id = tc.instructor_id AND ts.term_id = tc.term_id
        WHERE tc.course_code = %s
    """, (norm,))
    def _ratio(w, r):
        w = float((agg or {}).get(w) or 0); r = float((agg or {}).get(r) or 0)
        return round(w / r, 2) if r > 0 else None
    # recent professors + last-taught term, newest first by term sort key
    rows = query_fn("""
        SELECT DISTINCT instructor_first_name, instructor_last_name, term_title
        FROM trace_courses WHERE course_code = %s
    """, (norm,))
    def _term_key(t):
        # crude recency: a 4-digit year dominates, season breaks ties (Fall>Summer>Spring)
        t = (t or "").lower()
        yr = re.search(r"(\d{4})", t)
        season = 3 if "fall" in t else 2 if "summer" in t else 1 if "spring" in t else 0
        return (int(yr.group(1)) if yr else 0, season)
    last_taught, recent = "", []
    seen_names = set()
    for r in sorted(rows, key=lambda r: _term_key(r.get("term_title")), reverse=True):
        if not last_taught:
            last_taught = r.get("term_title") or ""
        nm = f"{(r.get('instructor_first_name') or '').strip()} {(r.get('instructor_last_name') or '').strip()}".strip()
        if nm and nm not in seen_names:
            seen_names.add(nm); recent.append(nm)
        if len(recent) >= 5:
            break
    # per-instructor breakdown: rating / difficulty / hrs-week for each professor who has
    # taught this course, same weighted-aggregation as the course-grain figures above.
    irows = query_fn("""
        SELECT
          tc.instructor_first_name AS fn, tc.instructor_last_name AS ln,
          SUM(CASE WHEN lower(ts.question) LIKE '%%overall%%' THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS o_w,
          SUM(CASE WHEN lower(ts.question) LIKE '%%overall%%' THEN CAST(ts.total_responses AS INT) ELSE 0 END) AS o_r,
          SUM(CASE WHEN lower(ts.question) LIKE '%%challeng%%' THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS c_w,
          SUM(CASE WHEN lower(ts.question) LIKE '%%challeng%%' THEN CAST(ts.total_responses AS INT) ELSE 0 END) AS c_r,
          SUM(CASE WHEN lower(ts.question) LIKE '%%hours%%' THEN CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT) ELSE 0 END) AS h_w,
          SUM(CASE WHEN lower(ts.question) LIKE '%%hours%%' THEN CAST(ts.total_responses AS INT) ELSE 0 END) AS h_r
        FROM trace_scores ts
        JOIN trace_courses tc ON ts.course_id = tc.course_id
          AND ts.instructor_id = tc.instructor_id AND ts.term_id = tc.term_id
        WHERE tc.course_code = %s
        GROUP BY tc.instructor_first_name, tc.instructor_last_name
    """, (norm,))
    def _row_ratio(row, w, r):
        w = float(row.get(w) or 0); r = float(row.get(r) or 0)
        return round(w / r, 2) if r > 0 else None
    breakdown = []
    for r in irows:
        nm = f"{(r.get('fn') or '').strip()} {(r.get('ln') or '').strip()}".strip()
        rating = _row_ratio(r, "o_w", "o_r")
        if not nm or rating is None:  # skip instructors with no overall-rating responses
            continue
        breakdown.append({
            "name": nm, "rating": rating,
            "difficulty": _row_ratio(r, "c_w", "c_r"),
            "hours_per_week": _row_ratio(r, "h_w", "h_r"),
        })
    breakdown.sort(key=lambda b: b["rating"], reverse=True)
    return {
        "kind": "course",
        "code": cat.get("code"), "name": cat.get("name"), "department": cat.get("department"),
        "avg_rating": _ratio("o_w", "o_r"),
        "avg_difficulty": _ratio("c_w", "c_r"),
        "hours_per_week": _ratio("h_w", "h_r"),
        "last_taught": last_taught,
        "recent_professors": recent,
        "instructor_breakdown": breakdown,
    }

def fetch_comments(slug, query_fn, limit=8):
    rows = query_fn("""
        SELECT t.source_id, t.body, t.subreddit, t.permalink, t.created_utc,
               t.score AS reddit_score, s.sentiment, s.score AS sentiment_score
        FROM reddit_mentions m
        JOIN reddit_text t ON t.source_id = m.source_id
        LEFT JOIN reddit_sentiment s
          ON s.source_id = t.source_id AND s.professor_slug = m.professor_slug
        WHERE m.professor_slug = %s AND t.flagged = false
        ORDER BY t.score DESC NULLS LAST
        LIMIT %s
    """, (slug, limit))
    out = []
    for r in rows:
        out.append({"source_id": r["source_id"], "body": r.get("body") or "",
                    "sentiment": r.get("sentiment"), "sentiment_score": r.get("sentiment_score"),
                    "score": r.get("reddit_score"),
                    "subreddit": r.get("subreddit"), "permalink": r.get("permalink"),
                    "created_utc": r.get("created_utc")})
    return out

def fetch_course_comments(code, query_fn, limit=8):
    """Reddit discussion for a course. Reddit text isn't linked to courses in the DB, so
    match the course code via full-text search. Require the code as a contiguous PHRASE
    (phraseto_tsquery uses FOLLOWED BY), not an AND of scattered tokens — otherwise an
    off-topic comment that merely contains 'cs' and '3100' somewhere matches as a bogus
    source. Reddit writes the code either way, so match the unspaced AND spaced spelling."""
    unspaced = re.sub(r"\s+", "", str(code).strip())
    spaced = re.sub(r"^([A-Za-z]{2,5})\s?(\d{4})$", r"\1 \2", unspaced)
    rows = query_fn("""
        SELECT t.source_id, t.body, t.subreddit, t.permalink, t.created_utc,
               t.score AS reddit_score,
               GREATEST(ts_rank(t.body_tsv, phraseto_tsquery('english', %s)),
                        ts_rank(t.body_tsv, phraseto_tsquery('english', %s))) AS rank
        FROM reddit_text t
        WHERE t.flagged = false
          AND (t.body_tsv @@ phraseto_tsquery('english', %s)
               OR t.body_tsv @@ phraseto_tsquery('english', %s))
        ORDER BY rank DESC
        LIMIT %s
    """, (unspaced, spaced, unspaced, spaced, limit))
    out = []
    for r in rows:
        out.append({"source_id": r["source_id"], "body": r.get("body") or "",
                    "sentiment": None, "sentiment_score": None,
                    "score": r.get("reddit_score"),
                    "subreddit": r.get("subreddit"), "permalink": r.get("permalink"),
                    "created_utc": r.get("created_utc")})
    return out

def fetch_reddit_mentions(slug, query_fn):
    rows = query_fn("""
        SELECT t.body, t.subreddit, t.permalink, t.created_utc,
               t.score AS reddit_score, s.sentiment, s.score AS sentiment_score
        FROM reddit_mentions m
        JOIN reddit_text t ON t.source_id = m.source_id
        LEFT JOIN reddit_sentiment s
          ON s.source_id = t.source_id AND s.professor_slug = m.professor_slug
        WHERE m.professor_slug = %s AND t.flagged = false
        ORDER BY t.created_utc DESC NULLS LAST
    """, (slug,))
    out = []
    for r in rows:
        out.append({
            "body": r.get("body") or "",
            "sentiment": r.get("sentiment"),
            "sentiment_score": r.get("sentiment_score"),
            "score": r.get("reddit_score"),
            "subreddit": r.get("subreddit"),
            "permalink": r.get("permalink"),
            "created_utc": r.get("created_utc"),
        })
    return out

def retrieve(query, hint, query_fn, query_one_fn, prof_search_fn, limit=8):
    topic = is_course_topic_query(query)
    if topic:
        with_ratings = wants_ratings(query)
        courses = fetch_courses_by_topic(topic, query_fn, limit=limit, with_ratings=with_ratings)
        if courses:
            return {"kind": "course_list", "topic": topic, "courses": courses,
                    "course_count": len(courses), "with_ratings": with_ratings,
                    "entity_key": f"topic:{topic}", "course_code": None,
                    "professor_slug": None, "facts": {}, "comments": [], "comment_count": 0}
        # topic phrasing but no catalog match → fall through to normal resolution

    # Course path: a course-code hint (e.g. "DS3000") resolves to course facts + that
    # course's Reddit discussion, instead of trying to find a professor by that name.
    course_term = next((t for t in (hint, query) if is_course_code(t)), None)
    if course_term:
        cfacts = fetch_course_facts(course_term, query_one_fn, query_fn)
        if cfacts:
            code = cfacts["code"]
            comments = fetch_course_comments(code, query_fn, limit=limit)
            return {"professor_slug": None, "course_code": code, "entity_key": code,
                    "entity_name": cfacts.get("name"), "facts": cfacts,
                    "comments": comments, "comment_count": len(comments)}
        # unknown course code → fall through to professor resolution

    ent = resolve_entity(query, hint, prof_search_fn, limit=1)
    if not ent:
        return {"professor_slug": None, "course_code": None, "entity_key": None,
                "entity_name": None, "facts": {}, "comments": [], "comment_count": 0}
    slug = ent["slug"]
    comments = fetch_comments(slug, query_fn, limit=limit)
    return {"professor_slug": slug, "course_code": None, "entity_key": slug,
            "professor_name": ent.get("name"), "entity_name": ent.get("name"),
            "facts": fetch_facts(slug, query_one_fn, query_fn),
            "comments": comments, "comment_count": len(comments)}

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    def prof_search_fn(q, limit=1):
        return [{"slug": "guha-prof", "name": "Olin Guha", "name_key": "olin guha",
                 "department": "Khoury", "avg_rating": 4.2, "total_reviews": 31}]

    def query_one_fn(sql, params):
        check("facts query parameterized", "%s" in sql)
        if "professors_catalog" in sql:
            check("facts query selects avg_hours", "avg_hours" in sql)
            return {"slug": "guha-prof", "name_key": "olin guha", "name": "Olin Guha",
                    "department": "Khoury", "rmp_rating": 4.1, "trace_rating": 4.3,
                    "avg_rating": 4.2, "difficulty": 3.5, "would_take_again_pct": 88.0,
                    "total_reviews": 31, "avg_hours": 7.5}
        if "rmp_reviews" in sql or "trace_comments" in sql:  # comment-count UNION
            return {"cnt": 42}
        return None

    def query_fn(sql, params):
        if "DISTINCT display_name" in sql:
            check("course-list query keyed on name_key (not subquery)", "name_key = %s" in sql)
            # real display_name carries section + instructor; two sections of the SAME
            # course must collapse to one clean "CODE Name" entry (no section/term/instructor)
            return [{"display_name": "CS3500:01 (Object-Oriented Design) - Olin Guha"},
                    {"display_name": "CS3500:02 (Object-Oriented Design) - Olin Guha"}]
        if "reddit_mentions" in sql:
            check("comments exclude flagged rows", "flagged = false" in sql or "NOT flagged" in sql)
            check("comments never select author/username", "author" not in sql.lower())
            return [{"source_id": "c1", "body": "hard but fair", "sentiment": "positive",
                     "sentiment_score": 0.8, "reddit_score": 12, "subreddit": "NEU",
                     "permalink": "/r/x", "created_utc": None}]
        return []

    r = retrieve("is guha hard", "Guha", query_fn, query_one_fn, prof_search_fn, limit=8)
    check("resolved a professor", r["professor_slug"] == "guha-prof")
    check("entity_key is the slug for a professor", r["entity_key"] == "guha-prof")
    check("facts kind is professor", r["facts"]["kind"] == "professor")
    check("courses taught strip section/term/instructor + dedupe",
          r["facts"]["courses"] == ["CS3500 Object-Oriented Design"])
    # the raw section/instructor must NOT leak into the courses-taught list
    check("courses taught omit section number", ":01" not in r["facts"]["courses"][0])
    check("courses taught omit instructor name", "Guha" not in r["facts"]["courses"][0])
    check("facts carry difficulty", r["facts"]["difficulty"] == 3.5)
    check("facts carry hours_per_week", r["facts"]["hours_per_week"] == 7.5)
    check("facts carry total_comments", r["facts"]["total_comments"] == 42)
    check("comments retrieved", r["comment_count"] == 1 and r["comments"][0]["sentiment"] == "positive")
    check("comment score is the reddit upvote score", r["comments"][0]["score"] == 12)
    check("comment keeps numeric sentiment separately", r["comments"][0]["sentiment_score"] == 0.8)

    none = retrieve("is guha hard", None, query_fn, lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("no entity resolves to empty result", none["professor_slug"] is None and none["comment_count"] == 0)

    # ── is_course_code ──
    check("is_course_code matches DS3000", is_course_code("DS3000") is True)
    check("is_course_code matches spaced 'CS 3000'", is_course_code("CS 3000") is True)
    check("is_course_code rejects a name", is_course_code("Olin Guha") is False)
    check("is_course_code rejects None", is_course_code(None) is False)

    # ── course path ──
    def course_query_one(sql, params):
        if "course_catalog" in sql:
            return {"code": "DS3000", "name": "Foundations of Data Science", "department": "Khoury"}
        if "trace_scores" in sql:  # weighted aggregation
            check("course agg joins trace_courses on course_code", "course_code = %s" in sql)
            return {"o_w": 8.0, "o_r": 2, "c_w": 6.0, "c_r": 2, "h_w": 14.0, "h_r": 2}
        return None
    def course_query(sql, params):
        if "trace_scores" in sql and "GROUP BY" in sql:  # per-instructor breakdown
            check("breakdown groups per instructor", "GROUP BY tc.instructor_first_name" in sql)
            return [
                {"fn": "Jan", "ln": "Vitek", "o_w": 8.0, "o_r": 2, "c_w": 6.0, "c_r": 2, "h_w": 14.0, "h_r": 2},
                {"fn": "Nick", "ln": "Brown", "o_w": 3.0, "o_r": 1, "c_w": 5.0, "c_r": 1, "h_w": 9.0, "h_r": 1},
                # an instructor with no overall-rating responses must be dropped, not shown as unknown
                {"fn": "Ghost", "ln": "Prof", "o_w": 0.0, "o_r": 0, "c_w": 4.0, "c_r": 1, "h_w": 4.0, "h_r": 1},
            ]
        if "instructor_first_name" in sql and "course_code = %s" in sql:
            return [
                {"instructor_first_name": "Jan", "instructor_last_name": "Vitek", "term_title": "Fall 2024"},
                {"instructor_first_name": "Nick", "instructor_last_name": "Brown", "term_title": "Spring 2023"},
            ]
        if "reddit_text" in sql:  # course comments full-text
            # Must require the code as a contiguous PHRASE (CRDB phraseto_tsquery), not a
            # plainto_tsquery AND of scattered tokens — otherwise an off-topic comment that
            # merely contains "cs" and "3100" somewhere gets pulled in as a bogus source.
            check("course comments use phraseto_tsquery (exact phrase, CRDB-safe)",
                  "phraseto_tsquery" in sql and "plainto_tsquery" not in sql)
            check("course comments avoid unsupported websearch_to_tsquery",
                  "websearch_to_tsquery" not in sql)
            return [{"source_id": "x1", "body": "DS3000 is a lot of work", "reddit_score": 5,
                     "subreddit": "NEU", "permalink": "/r/z", "created_utc": None}]
        return []
    rc = retrieve("tell me about DS3000", "DS3000", course_query, course_query_one, prof_search_fn, limit=8)
    check("course path sets course_code", rc["course_code"] == "DS3000")
    check("course path entity_key is the code", rc["entity_key"] == "DS3000")
    check("course facts kind is course", rc["facts"]["kind"] == "course")
    check("course avg_rating computed (8/2)", rc["facts"]["avg_rating"] == 4.0)
    check("course avg_difficulty computed (6/2)", rc["facts"]["avg_difficulty"] == 3.0)
    check("course hours_per_week computed (14/2)", rc["facts"]["hours_per_week"] == 7.0)
    check("course last_taught is newest term", rc["facts"]["last_taught"] == "Fall 2024")
    check("course recent_professors newest-first", rc["facts"]["recent_professors"][0] == "Jan Vitek")
    check("course Reddit comments fetched", rc["comment_count"] == 1)

    # ── per-instructor breakdown (rating / difficulty / hrs-week) ──
    bd = rc["facts"]["instructor_breakdown"]
    check("breakdown drops instructors with no rating", len(bd) == 2)
    check("breakdown sorted by rating desc", bd[0]["name"] == "Jan Vitek" and bd[0]["rating"] == 4.0)
    check("breakdown carries difficulty (6/2)", bd[0]["difficulty"] == 3.0)
    check("breakdown carries hours/week (14/2)", bd[0]["hours_per_week"] == 7.0)
    check("breakdown second instructor", bd[1]["name"] == "Nick Brown" and bd[1]["rating"] == 3.0)

    # ── fetch_course_comments: exact-phrase matching for BOTH spellings ──
    # A comment is a valid source only if it contains the code as a contiguous phrase.
    # Reddit writes it either way ("CS3100" / "CS 3100"), so both phrasings must be queried.
    cap = {}
    def phrase_query(sql, params):
        cap["sql"] = sql; cap["params"] = list(params)
        return [{"source_id": "x1", "body": "CS3100 is rough", "reddit_score": 3,
                 "subreddit": "NEU", "permalink": "/r/p", "created_utc": None}]
    cc = fetch_course_comments("CS3100", phrase_query, limit=8)
    check("course comments fetched", len(cc) == 1)
    check("fetch_course_comments uses phraseto_tsquery only",
          "phraseto_tsquery" in cap["sql"] and "plainto_tsquery" not in cap["sql"])
    check("fetch_course_comments queries unspaced spelling 'CS3100'", "CS3100" in cap["params"])
    check("fetch_course_comments queries spaced spelling 'CS 3100'", "CS 3100" in cap["params"])

    # unknown course code falls through to professor resolution (still returns a prof here)
    def unknown_course_one(sql, params):
        if "course_catalog" in sql:
            return None
        return query_one_fn(sql, params)
    ru = retrieve("about ZZ9999", "ZZ9999", query_fn, unknown_course_one, prof_search_fn, limit=8)
    check("unknown course code falls through to professor", ru["professor_slug"] == "guha-prof")

    # ── fetch_reddit_mentions (professor-page path) ──
    captured = {}
    def capture_query_fn(sql, params):
        captured["sql"] = sql
        return query_fn(sql, params)
    rm = fetch_reddit_mentions("guha-prof", capture_query_fn)
    check("reddit mentions query parameterized", "%s" in captured["sql"])
    check("reddit mentions exclude flagged", "flagged = false" in captured["sql"])
    check("reddit mentions never select author", "author" not in captured["sql"].lower())
    check("reddit mentions ordered newest-first", "created_utc DESC" in captured["sql"])
    check("reddit mentions shape carries body", rm and rm[0]["body"] == "hard but fair")
    check("reddit mentions carry sentiment", rm[0]["sentiment"] == "positive")
    check("reddit mentions carry upvote score", rm[0]["score"] == 12)
    check("reddit mentions carry sentiment_score", rm[0]["sentiment_score"] == 0.8)
    check("reddit mentions omit source_id", "source_id" not in rm[0])

    def hint_only_search(term, limit=1):
        return [{"slug": "guha-prof", "name": "Olin Guha"}] if term == "Guha" else []
    rh = retrieve("is the hard one good", "Guha", query_fn, query_one_fn, hint_only_search, limit=8)
    check("resolve_entity prefers the hint", rh["professor_slug"] == "guha-prof")

    def query_only_search(term, limit=1):
        return [{"slug": "guha-prof", "name": "Olin Guha"}] if term == "olin guha review" else []
    rq = retrieve("olin guha review", None, query_fn, query_one_fn, query_only_search, limit=8)
    check("resolve_entity falls back to the raw query", rq["professor_slug"] == "guha-prof")

    # ── topic-course listing ──
    check("topic query: 'what database courses are there' -> 'database'",
          is_course_topic_query("what database courses are there") == "database")
    check("topic query: 'which CS classes are there' -> 'cs'",
          is_course_topic_query("which cs classes are there") == "cs")
    check("topic query: 'courses about machine learning' -> 'machine learning'",
          is_course_topic_query("courses about machine learning") == "machine learning")
    check("topic query: strips leading article",
          is_course_topic_query("what are the database courses") == "database")
    check("topic query: strips leading rating adjective 'best'",
          is_course_topic_query("what are the best database courses") == "database")
    check("topic query: strips leading 'top' adjective",
          is_course_topic_query("which top cs classes are there") == "cs")
    check("topic query: keeps subject when rating word is internal-only",
          is_course_topic_query("what machine learning courses are there") == "machine learning")
    check("topic query: rejects a specific course code question",
          is_course_topic_query("what is CS3500") is None)
    check("topic query: rejects a professor question", is_course_topic_query("is guha hard") is None)
    check("topic query: rejects bare name", is_course_topic_query("Olin Guha") is None)

    check("wants_ratings true on 'which is best'",
          wants_ratings("what database courses are there and which is best") is True)
    check("wants_ratings true on 'highest rated'", wants_ratings("highest rated cs courses") is True)
    check("wants_ratings false on plain listing", wants_ratings("what database courses are there") is False)

    # fetch_courses_by_topic: default = one catalog query, no per-course query
    topic_calls = []
    def topic_query(sql, params):
        topic_calls.append(sql)
        if "course_catalog" in sql:
            check("topic search uses search_text LIKE", "search_text LIKE %s" in sql)
            check("topic search orders code-prefix first", "lower(code) LIKE %s" in sql)
            return [{"code": "CS3200", "name": "Database Design", "department": "Khoury"},
                    {"code": "DS3000", "name": "Foundations of Data Science", "department": "Khoury"}]
        raise AssertionError("default topic search must not issue per-course queries")
    courses = fetch_courses_by_topic("database", topic_query, limit=8)
    check("topic search returns matched courses", [c["code"] for c in courses] == ["CS3200", "DS3000"])
    check("default topic search issues exactly one query", len(topic_calls) == 1)
    check("default courses carry no rating", "rating" not in courses[0])

    # with_ratings=True: attaches a rating per course and re-sorts desc
    def topic_query_rated(sql, params):
        if "course_catalog" in sql:
            return [{"code": "CS3200", "name": "Database Design", "department": "Khoury"},
                    {"code": "DS3000", "name": "Foundations of Data Science", "department": "Khoury"}]
        if "trace_scores" in sql:  # per-course overall rating
            code = params[0]
            return [{"o_w": 8.0, "o_r": 2}] if code == "CS3200" else [{"o_w": 9.0, "o_r": 2}]
        return []
    rated = fetch_courses_by_topic("database", topic_query_rated, limit=8, with_ratings=True)
    check("rated courses carry a rating", all("rating" in c for c in rated))
    check("rated courses sorted by rating desc", [c["code"] for c in rated] == ["DS3000", "CS3200"])
    check("rated values computed (8/2, 9/2)", rated[0]["rating"] == 4.5 and rated[1]["rating"] == 4.0)

    # retrieve() returns a course_list block on a topic query (hint is None — gate found no entity)
    def topic_retrieve_query(sql, params):
        if "course_catalog" in sql and "search_text LIKE" in sql:
            return [{"code": "CS3200", "name": "Database Design", "department": "Khoury"}]
        return []
    rb = retrieve("what database courses are there", None, topic_retrieve_query,
                  lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("retrieve returns course_list kind", rb["kind"] == "course_list")
    check("retrieve course_list topic", rb["topic"] == "database")
    check("retrieve course_list carries courses", rb["courses"][0]["code"] == "CS3200")
    check("retrieve course_list entity_key tagged", rb["entity_key"] == "topic:database")
    check("retrieve course_list with_ratings false by default", rb["with_ratings"] is False)

    # topic regex fires but 0 courses match -> fall through (NOT a course_list block)
    rb0 = retrieve("what zzzz courses are there", None,
                   lambda sql, params: [], lambda s, p=None: None, lambda q, limit=1: [], limit=8)
    check("zero-match topic falls through (no course_list)", rb0.get("kind") != "course_list")

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Question-path retrieval (structured-first).")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
