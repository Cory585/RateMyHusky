import os, sys, json, argparse

KEY_STORE = os.path.join(os.path.dirname(__file__), "groq_keys.local.json")
DEFAULT_RPD = 1000
DEFAULT_TPD = 200000  # synthesis model (gpt-oss-120b) free-tier TPD; TPD binds before RPD

def _entry(key):
    return {"key": key, "provider": "groq", "rpd_limit": DEFAULT_RPD, "tpd_limit": DEFAULT_TPD}

def load_entries():
    # Preferred: lowercase groq1, groq2, ... groqN env vars (contiguous from 1).
    env_keys = []
    n = 1
    while True:
        val = os.getenv("groq%d" % n)
        if not val:
            break
        env_keys.append(_entry(val))
        n += 1
    if env_keys:
        return env_keys
    # Fallback: gitignored JSON store.
    if os.path.exists(KEY_STORE):
        with open(KEY_STORE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("keys", data) if isinstance(data, dict) else data
    # Last resort: a single GROQ_API_KEY.
    env_key = os.getenv("GROQ_API_KEY")
    if env_key:
        return [_entry(env_key)]
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

    def acquire(self, est_tokens):
        n = len(self.entries)
        for offset in range(n):
            i = (self.idx + offset) % n
            if self._covers(self.entries[i], est_tokens):
                self.idx = i
                return self.entries[i]
        return None

    def retire(self, key):
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
    # k1 is at its RPD limit; acquire must advance to k2 before returning.
    usage = {"k1": {"rpd": 1000, "tpd": 10}, "k2": {"rpd": 0, "tpd": 0}}
    pool = KeyPool(entries, usage_fn=lambda k: usage[k])
    chosen = pool.acquire(est_tokens=1800)
    check("acquire advances past over-limit RPD entry", chosen and chosen["key"] == "k2")

    # Both entries clear the RPD check but would exceed tpd_limit with this
    # request, so the TPD predicate alone leaves no usable entry.
    usage2 = {"k1": {"rpd": 0, "tpd": 499999}, "k2": {"rpd": 0, "tpd": 499999}}
    pool2 = KeyPool(entries, usage_fn=lambda k: usage2[k])
    check("TPD overflow leaves no usable entry",
          pool2.acquire(est_tokens=1800) is None)

    # retire the current entry, then acquire resolves to the other one.
    usage3 = {"k1": {"rpd": 0, "tpd": 0}, "k2": {"rpd": 0, "tpd": 0}}
    pool3 = KeyPool(entries, usage_fn=lambda k: usage3[k])
    first = pool3.acquire(est_tokens=10)
    pool3.retire(first["key"])
    second = pool3.acquire(est_tokens=10)
    check("acquire skips a retired entry", second["key"] != first["key"])

    check("empty pool returns None", KeyPool([]).acquire(10) is None)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Provider-agnostic LLM access provider.")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
