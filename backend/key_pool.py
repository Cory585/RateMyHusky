import os, sys, json, argparse

KEY_STORE = os.path.join(os.path.dirname(__file__), "groq_keys.local.json")
DEFAULT_RPD = 1000
DEFAULT_TPD = 500000

def load_entries():
    if os.path.exists(KEY_STORE):
        with open(KEY_STORE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("keys", data) if isinstance(data, dict) else data
    env_key = os.getenv("GROQ_API_KEY")
    if env_key:
        return [{"key": env_key, "provider": "groq",
                 "rpd_limit": DEFAULT_RPD, "tpd_limit": DEFAULT_TPD}]
    return []

def _zero_usage(_key):
    return {"rpd": 0, "tpd": 0}

class KeyPool:
    def __init__(self, entries=None, usage_fn=None):
        self.entries = entries if entries is not None else load_entries()
        self.usage_fn = usage_fn or _zero_usage
        self.idx = 0
        self.exhausted = set()

    def _covers(self, entry, est_tokens):
        if entry["key"] in self.exhausted:
            return False
        u = self.usage_fn(entry["key"])
        if u["rpd"] >= entry["rpd_limit"]:
            return False
        if u["tpd"] + est_tokens > entry["tpd_limit"]:
            return False
        return True

    def current(self):
        return self.entries[self.idx] if 0 <= self.idx < len(self.entries) else None

    def pick_for_request(self, est_tokens):
        n = len(self.entries)
        for offset in range(n):
            i = (self.idx + offset) % n
            if self._covers(self.entries[i], est_tokens):
                self.idx = i
                return self.entries[i]
        return None

    def mark_exhausted(self, key):
        self.exhausted.add(key)
        if self.current() and self.current()["key"] == key:
            self.idx = (self.idx + 1) % max(len(self.entries), 1)

    def reset_daily(self):
        self.exhausted.clear()

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    entries = [
        {"key": "k1", "provider": "groq", "rpd_limit": 1000, "tpd_limit": 500000},
        {"key": "k2", "provider": "groq", "rpd_limit": 1000, "tpd_limit": 500000},
    ]
    # k1 is nearly out of RPD; predictive rotation must skip to k2 before the call.
    usage = {"k1": {"rpd": 1000, "tpd": 10}, "k2": {"rpd": 0, "tpd": 0}}
    pool = KeyPool(entries, usage_fn=lambda k: usage[k])
    chosen = pool.pick_for_request(est_tokens=1800)
    check("predictive rotation skips exhausted-RPD key", chosen and chosen["key"] == "k2")

    # TPD prediction: both keys clear the RPD gate but would exceed tpd_limit
    # with this request → the TPD predicate alone exhausts the pool.
    usage2 = {"k1": {"rpd": 0, "tpd": 499999}, "k2": {"rpd": 0, "tpd": 499999}}
    pool2 = KeyPool(entries, usage_fn=lambda k: usage2[k])
    check("predictive TPD overflow exhausts pool",
          pool2.pick_for_request(est_tokens=1800) is None)

    # Reactive backstop: mark current exhausted, rotate, retry resolves to the other key.
    usage3 = {"k1": {"rpd": 0, "tpd": 0}, "k2": {"rpd": 0, "tpd": 0}}
    pool3 = KeyPool(entries, usage_fn=lambda k: usage3[k])
    first = pool3.pick_for_request(est_tokens=10)
    pool3.mark_exhausted(first["key"])
    second = pool3.pick_for_request(est_tokens=10)
    check("reactive backstop rotates off exhausted key", second["key"] != first["key"])

    check("empty pool returns None", KeyPool([]).pick_for_request(10) is None)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Provider-agnostic LLM key-rotation pool.")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
