import sys, argparse
from datetime import datetime, timezone, timedelta

PER_KEY_DAILY = 100
SAFE_THRESHOLD = 0.85
CONSTRAINED_SESSION_CAP = 3

def daily_budget(num_keys):
    return PER_KEY_DAILY * max(num_keys, 1)

def _today_ok_count(query_one_fn):
    row = query_one_fn(
        "SELECT count(*) AS c FROM ask_log "
        "WHERE date_trunc('day', created_at) = current_date AND result_status = 'ok'")
    return (row or {}).get("c", 0) or 0

def projected_daily_usage(query_one_fn, now=None):
    now = now or datetime.now(timezone.utc)
    minute_of_day = now.hour * 60 + now.minute
    if minute_of_day < 30:
        return 0
    calls = _today_ok_count(query_one_fn)
    return int((calls / minute_of_day) * 1440)

def is_constrained(query_one_fn, num_keys, now=None):
    return projected_daily_usage(query_one_fn, now) > daily_budget(num_keys) * SAFE_THRESHOLD

def global_budget_hit(query_one_fn, num_keys):
    return _today_ok_count(query_one_fn) >= daily_budget(num_keys)

def session_allowed(session_token, query_one_fn, num_keys, now=None):
    if not is_constrained(query_one_fn, num_keys, now):
        return True, None
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=60)
    row = query_one_fn(
        "SELECT count(*) AS c FROM ask_log "
        "WHERE session_token = %s AND created_at > %s AND result_status = 'ok'",
        (session_token, cutoff))
    if ((row or {}).get("c", 0) or 0) >= CONSTRAINED_SESSION_CAP:
        return False, "rate_limited"
    return True, None

def _count_adapter(fn):
    # adapt the test's dict-returning fn to query_one_fn shape
    return fn

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    noon = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)  # minute_of_day = 720

    # budget scales with key count: 1 key -> 100/day, 3 keys -> 300/day
    check("budget scales per key", daily_budget(1) == 100 and daily_budget(3) == 300)
    check("budget floors at 1 key", daily_budget(0) == 100)

    # 3 keys -> budget 300, threshold 0.85*300 = 255.
    # 150 ok-calls by noon -> rate 150/720/min -> *1440 = 300 projected > 255 -> constrained
    def q1(sql, params=None): return {"c": 150}
    check("busy day projects over threshold", is_constrained(_count_adapter(q1), num_keys=3, now=noon) is True)

    def q0(sql, params=None): return {"c": 5}
    check("quiet day not constrained", is_constrained(_count_adapter(q0), num_keys=3, now=noon) is False)

    # global backstop at the pool budget (3 keys -> 300)
    def qfull(sql, params=None): return {"c": 300}
    check("global budget hit at pool ceiling", global_budget_hit(_count_adapter(qfull), num_keys=3) is True)

    # per-session cap only bites in constrained mode
    def q_session(sql, params=None):
        # return projection-high for the day count, and 3 recent for the session count
        return {"c": 150} if "current_date" in sql else {"c": 3}
    ok, status = session_allowed("sess1", _count_adapter(q_session), num_keys=3, now=noon)
    check("constrained session over cap denied", ok is False and status == "rate_limited")

    # normal (quiet) mode: session is always allowed
    ok2, status2 = session_allowed("sess1", _count_adapter(q0), num_keys=3, now=noon)
    check("normal-mode session allowed", ok2 is True and status2 is None)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Adaptive question-path throttle.")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
