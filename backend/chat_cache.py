import sys, re, hashlib, argparse

_STOP = {"is", "a", "an", "the", "of", "for", "to", "do", "does", "how", "what", "are"}

def normalize_query(q):
    q = re.sub(r"[^\w\s]", " ", (q or "").lower())
    toks = [t for t in q.split() if t and t not in _STOP]
    return " ".join(sorted(toks))

def cache_key(q, professor_slug):
    # NUL separator can't appear in a normalized query or a slug, so distinct
    # (query, slug) pairs can never collide into one key (wrong-professor answer).
    raw = normalize_query(q) + "\x00" + (professor_slug or "")
    return "chatq:" + hashlib.sha256(raw.encode()).hexdigest()[:24]

def get_cached(q, slug, cache_get_fn):
    return cache_get_fn(cache_key(q, slug))

def set_cached(q, slug, answer_payload, cache_set_fn):
    cache_set_fn(cache_key(q, slug), answer_payload)

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    check("normalize collapses variants",
          normalize_query("Is Guha  HARD?") == normalize_query("guha is hard"))
    check("all-stopword query normalizes empty", normalize_query("the a an of how") == "")
    k1 = cache_key("is guha hard", "guha-prof")
    k2 = cache_key("Guha is hard?", "guha-prof")
    check("equivalent queries share a key", k1 == k2)
    check("different prof -> different key", cache_key("is guha hard", "x") != k1)

    store = {}
    set_cached("is guha hard", "guha-prof", {"answer": "fair"}, lambda k, v: store.__setitem__(k, v))
    got = get_cached("Guha is hard?", "guha-prof", lambda k: store.get(k))
    check("round-trips a cached answer", got == {"answer": "fair"})
    check("miss returns None", get_cached("totally other q", None, lambda k: store.get(k)) is None)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Semantic answer cache for the question path.")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
