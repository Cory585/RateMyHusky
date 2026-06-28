import sys, argparse

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
        SELECT slug, name, department, rmp_rating, trace_rating, avg_rating,
               difficulty, would_take_again_pct, total_reviews
        FROM professors_catalog WHERE slug = %s
    """, (slug,))
    if not prof:
        return {}
    course_rows = query_fn("""
        SELECT DISTINCT display_name FROM trace_courses
        WHERE name_key = (SELECT name_key FROM professors_catalog WHERE slug = %s)
          AND display_name IS NOT NULL
        ORDER BY display_name LIMIT 25
    """, (slug,))
    seen, courses = set(), []
    for c in course_rows:
        dn = c.get("display_name")
        if dn and dn not in seen:
            seen.add(dn); courses.append(dn)
    return {
        "name": prof.get("name"), "department": prof.get("department"),
        "rmp_rating": prof.get("rmp_rating"), "trace_rating": prof.get("trace_rating"),
        "avg_rating": prof.get("avg_rating"), "difficulty": prof.get("difficulty"),
        "would_take_again_pct": prof.get("would_take_again_pct"),
        "total_reviews": prof.get("total_reviews"), "courses": courses,
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

def retrieve(query, hint, query_fn, query_one_fn, prof_search_fn, limit=8):
    ent = resolve_entity(query, hint, prof_search_fn, limit=1)
    if not ent:
        return {"professor_slug": None, "professor_name": None,
                "facts": {}, "comments": [], "comment_count": 0}
    slug = ent["slug"]
    comments = fetch_comments(slug, query_fn, limit=limit)
    return {"professor_slug": slug, "professor_name": ent.get("name"),
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
        return {"slug": "guha-prof", "name": "Olin Guha", "department": "Khoury",
                "rmp_rating": 4.1, "trace_rating": 4.3, "avg_rating": 4.2,
                "difficulty": 3.5, "would_take_again_pct": 88.0, "total_reviews": 31}

    def query_fn(sql, params):
        if "trace_courses" in sql:
            return [{"display_name": "CS3500 OOD"}, {"display_name": "CS3500 OOD"}]
        if "reddit_mentions" in sql:
            check("comments exclude flagged rows", "flagged = false" in sql or "NOT flagged" in sql)
            check("comments never select author/username", "author" not in sql.lower())
            return [{"source_id": "c1", "body": "hard but fair", "sentiment": "positive",
                     "sentiment_score": 0.8, "reddit_score": 12, "subreddit": "NEU",
                     "permalink": "/r/x", "created_utc": None}]
        return []

    r = retrieve("is guha hard", "Guha", query_fn, query_one_fn, prof_search_fn, limit=8)
    check("resolved a professor", r["professor_slug"] == "guha-prof")
    check("facts carry courses (deduped)", r["facts"]["courses"] == ["CS3500 OOD"])
    check("facts carry difficulty", r["facts"]["difficulty"] == 3.5)
    check("comments retrieved", r["comment_count"] == 1 and r["comments"][0]["sentiment"] == "positive")
    check("comment score is the reddit upvote score", r["comments"][0]["score"] == 12)
    check("comment keeps numeric sentiment separately", r["comments"][0]["sentiment_score"] == 0.8)

    none = retrieve("is guha hard", None, query_fn, lambda s, p: None, lambda q, limit=1: [], limit=8)
    check("no entity resolves to empty result", none["professor_slug"] is None and none["comment_count"] == 0)

    def hint_only_search(term, limit=1):
        return [{"slug": "guha-prof", "name": "Olin Guha"}] if term == "Guha" else []
    rh = retrieve("is the hard one good", "Guha", query_fn, query_one_fn, hint_only_search, limit=8)
    check("resolve_entity prefers the hint", rh["professor_slug"] == "guha-prof")

    def query_only_search(term, limit=1):
        return [{"slug": "guha-prof", "name": "Olin Guha"}] if term == "olin guha review" else []
    rq = retrieve("olin guha review", None, query_fn, query_one_fn, query_only_search, limit=8)
    check("resolve_entity falls back to the raw query", rq["professor_slug"] == "guha-prof")

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
