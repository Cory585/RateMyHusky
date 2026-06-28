import sys, argparse

def keyword_search(q, query_fn, prof_search_fn, limit=20):
    rows = query_fn("""
        SELECT t.source_id, t.body, t.subreddit, t.permalink,
               array_agg(DISTINCT m.professor_slug) AS professor_slugs,
               ts_rank(t.body_tsv, plainto_tsquery('english', %s)) AS rank
        FROM reddit_text t
        JOIN reddit_mentions m ON m.source_id = t.source_id
        WHERE t.flagged = false
          AND t.body_tsv @@ plainto_tsquery('english', %s)
        GROUP BY t.source_id, t.body, t.subreddit, t.permalink, t.body_tsv
        ORDER BY rank DESC
        LIMIT %s
    """, (q, q, limit))

    if not rows:
        return {"comments": [], "professors": prof_search_fn(q, limit=5)}

    source_ids = [r["source_id"] for r in rows]
    sentiment_rows = query_fn("""
        SELECT source_id, professor_slug, sentiment, score
        FROM reddit_sentiment
        WHERE source_id = ANY(%s)
    """, (source_ids,))

    # Build per-source_id sentiment map: {source_id: {slug: {sentiment, score}}}
    sentiments_by_sid = {}
    for s in sentiment_rows:
        sid = s["source_id"]
        if sid not in sentiments_by_sid:
            sentiments_by_sid[sid] = {}
        sentiments_by_sid[sid][s["professor_slug"]] = {
            "sentiment": s.get("sentiment"),
            "score": s.get("score"),
        }

    comments = []
    for r in rows:
        body = r.get("body") or ""
        sid = r["source_id"]
        comments.append({
            "source_id": sid,
            "professor_slugs": list(r.get("professor_slugs") or []),
            "snippet": body[:240],
            "sentiments": sentiments_by_sid.get(sid, {}),
            "subreddit": r.get("subreddit"),
            "permalink": r.get("permalink"),
            "rank": r.get("rank"),
        })

    return {"comments": comments, "professors": prof_search_fn(q, limit=5)}

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    captured_main_sql = []

    def query_fn(sql, params):
        if "reddit_sentiment" in sql and "ANY(%s)" in sql:
            # Second query: sentiment by source_id
            return [
                {"source_id": "c1", "professor_slug": "ada-lovelace",
                 "sentiment": "positive", "score": 0.6},
            ]
        # Main deduped query
        captured_main_sql.append(sql)
        check("query excludes flagged rows", "flagged = false" in sql or "NOT flagged" in sql)
        check("query is parameterized", "%s" in sql)
        check("query uses CRDB-supported tsquery (plainto_tsquery)", "plainto_tsquery" in sql)
        check("query avoids unsupported websearch_to_tsquery", "websearch_to_tsquery" not in sql)
        return [
            {"source_id": "c1", "body": "great grader", "subreddit": "NEU",
             "permalink": "/r/x", "professor_slugs": ["ada-lovelace"], "rank": 0.9},
        ]

    def prof_search_fn(q, limit=5):
        return [{"slug": "ada-lovelace", "name": "Ada Lovelace"}]

    result = keyword_search("grader", query_fn, prof_search_fn, limit=20)
    check("returns dict with comments and professors", isinstance(result, dict)
          and "comments" in result and "professors" in result)
    check("comments is non-empty", len(result["comments"]) == 1)
    check("result has snippet", "snippet" in result["comments"][0])
    check("professor_slugs is a list", isinstance(result["comments"][0]["professor_slugs"], list))
    check("sentiments is a dict", isinstance(result["comments"][0]["sentiments"], dict))
    check("sentiment carries data for slug",
          result["comments"][0]["sentiments"].get("ada-lovelace", {}).get("sentiment") == "positive")
    check("professors == stub list", result["professors"] == [{"slug": "ada-lovelace", "name": "Ada Lovelace"}])
    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Reddit keyword search helper.")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
