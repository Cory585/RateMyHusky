import sys, re, hashlib, argparse

_STOP = {"is", "a", "an", "the", "of", "for", "to", "do", "does", "how", "what", "are"}

def normalize_query(q):
    q = re.sub(r"[^\w\s]", " ", (q or "").lower())
    toks = [t for t in q.split() if t and t not in _STOP]
    return " ".join(sorted(toks))

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
    # order-independent: "Wu and Rachlin" == "Rachlin and Wu"
    check("entity order does not affect key",
          cache_key("compare wu and rachlin", ["wu-prof", "rachlin-prof"])
          == cache_key("compare rachlin and wu", ["rachlin-prof", "wu-prof"]))

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
