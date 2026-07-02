import sys, re, hashlib, argparse

_STOP = {"is", "a", "an", "the", "of", "for", "to", "do", "does", "how", "what", "are"}

def normalize_query(q):
    # Order-preserving: word order carries meaning ("CS2500 before CS3500" != the reverse),
    # so tokens are NOT sorted -- only lowercased/depunctuated/whitespace-collapsed.
    q = re.sub(r"[^\w\s]", " ", (q or "").lower())
    toks = [t for t in q.split() if t and t not in _STOP]
    return " ".join(toks)

def cache_key(q, entity_keys):
    # entity_keys: list of slugs/codes. Sort so order ("Wu and Rachlin" vs
    # "Rachlin and Wu") doesn't fork the cache. NUL separates query from keys and
    # keys from each other, so distinct (query, entities) sets never collide.
    keys = "\x00".join(sorted(k for k in (entity_keys or []) if k))
    raw = normalize_query(q) + "\x00" + keys
    return "chatq:" + hashlib.sha256(raw.encode()).hexdigest()[:24]

def get_cached(q, entity_keys, cache_get_fn):
    return cache_get_fn(cache_key(q, entity_keys))

def set_cached(q, entity_keys, answer_payload, cache_set_fn):
    cache_set_fn(cache_key(q, entity_keys), answer_payload)

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    check("normalize collapses variants",
          normalize_query("Is Guha  HARD?") == normalize_query("guha is hard"))
    check("all-stopword query normalizes empty", normalize_query("the a an of how") == "")
    k1 = cache_key("is guha hard", ["guha-prof"])
    k2 = cache_key("Guha is hard?", ["guha-prof"])
    check("equivalent queries share a key", k1 == k2)
    check("different entity -> different key", cache_key("is guha hard", ["x"]) != k1)
    # entity_keys list order does not affect the key (only the entity_keys param order, not
    # the query text) -- same query text, entity_keys passed in each order.
    check("entity_keys param order does not affect key",
          cache_key("compare wu and rachlin", ["wu-prof", "rachlin-prof"])
          == cache_key("compare wu and rachlin", ["rachlin-prof", "wu-prof"]))
    # Issue 14: word order in the QUERY TEXT is meaningful and must NOT be sorted away --
    # opposite-direction questions get different keys so one user's answer never leaks to
    # the reverse-order question.
    check("directional queries produce different keys",
          cache_key("take CS2500 before CS3500?", ["cs2500", "cs3500"])
          != cache_key("take CS3500 before CS2500?", ["cs2500", "cs3500"]))
    # unrelated word-order/case/whitespace variation still collapses to the same key
    check("word-order-preserving normalize still collapses case/whitespace/punctuation",
          cache_key("Is  Guha   hard?", ["guha-prof"]) == cache_key("is guha hard", ["guha-prof"]))

    store = {}
    set_cached("is guha hard", ["guha-prof"], {"answer": "fair"}, lambda k, v: store.__setitem__(k, v))
    got = get_cached("Guha is hard?", ["guha-prof"], lambda k: store.get(k))
    check("round-trips a cached answer", got == {"answer": "fair"})
    check("miss returns None", get_cached("totally other q", [], lambda k: store.get(k)) is None)

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
