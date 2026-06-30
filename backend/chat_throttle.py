import sys, argparse
from datetime import datetime, timezone, timedelta

# ~80 = measured ceiling: gpt-oss-120b 200K TPD / ~2500 tokens per answered question.
# (Was 100, based on a 1800-tok estimate; live testing showed ~2500/Q, so 80 is the honest cap.)
PER_KEY_DAILY = 80
SAFE_THRESHOLD = 0.85
CONSTRAINED_SESSION_CAP = 3

# Per-minute (TPM) limits — the real concurrency bottleneck. gpt-oss-120b free tier = 8K tokens/min
# per key (confirmed from the Groq dashboard). Each answered question costs ~2500 tokens end-to-end
# (gate + synth, incl. reasoning), so one key sustains only ~3 questions/min.
TPM_PER_KEY = 8000
EST_TOKENS_PER_Q = 2500

def daily_budget(num_keys):
    return PER_KEY_DAILY * max(num_keys, 1)

def minute_token_budget(num_keys):
    return TPM_PER_KEY * max(num_keys, 1)

def tokens_used_last_minute(query_one_fn, now=None):
    """Sum tokens_used across the pool over the last 60s (from ask_log). Proactive TPM guard:
    lets the throttle degrade gracefully BEFORE Groq returns a 429."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=60)
    row = query_one_fn(
        "SELECT coalesce(sum(tokens_used), 0) AS t FROM ask_log WHERE created_at > %s",
        (cutoff,))
    return (row or {}).get("t", 0) or 0

def minute_capacity_ok(query_one_fn, num_keys, est_tokens=EST_TOKENS_PER_Q, now=None):
    """True if this question fits under the pool's per-minute token ceiling (with safety margin).
    False => the minute is saturated; the orchestrator must degrade to keyword fallback (no LLM call)."""
    used = tokens_used_last_minute(query_one_fn, now)
    return used + est_tokens <= minute_token_budget(num_keys) * SAFE_THRESHOLD

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

    # budget scales with key count: 1 key -> 80/day, 3 keys -> 240/day
    check("budget scales per key", daily_budget(1) == 80 and daily_budget(3) == 240)
    check("budget floors at 1 key", daily_budget(0) == 80)

    # 3 keys -> budget 240, threshold 0.85*240 = 204.
    # 120 ok-calls by noon -> rate 120/720/min -> *1440 = 240 projected > 204 -> constrained
    def q1(sql, params=None): return {"c": 120}
    check("busy day projects over threshold", is_constrained(_count_adapter(q1), num_keys=3, now=noon) is True)

    def q0(sql, params=None): return {"c": 5}
    check("quiet day not constrained", is_constrained(_count_adapter(q0), num_keys=3, now=noon) is False)

    # global backstop at the pool budget (3 keys -> 240)
    def qfull(sql, params=None): return {"c": 240}
    check("global budget hit at pool ceiling", global_budget_hit(_count_adapter(qfull), num_keys=3) is True)

    # per-session cap only bites in constrained mode
    def q_session(sql, params=None):
        # return projection-high for the day count, and 3 recent for the session count
        return {"c": 120} if "current_date" in sql else {"c": 3}
    ok, status = session_allowed("sess1", _count_adapter(q_session), num_keys=3, now=noon)
    check("constrained session over cap denied", ok is False and status == "rate_limited")

    # normal (quiet) mode: session is always allowed
    ok2, status2 = session_allowed("sess1", _count_adapter(q0), num_keys=3, now=noon)
    check("normal-mode session allowed", ok2 is True and status2 is None)

    # --- per-minute TPM throttle ---
    check("minute budget scales per key", minute_token_budget(1) == 8000 and minute_token_budget(3) == 24000)
    check("minute budget floors at 1 key", minute_token_budget(0) == 8000)

    # 3 keys -> 24000/min budget, 0.85*24000 = 20400 usable.
    # quiet minute (2000 tok used) + one ~2500-tok question = 4500 <= 20400 -> OK
    def q_quiet_min(sql, params=None): return {"t": 2000}
    check("quiet minute admits a question",
          minute_capacity_ok(_count_adapter(q_quiet_min), num_keys=3, now=noon) is True)

    # saturated minute (20000 tok used) + 2500 = 22500 > 20400 -> NOT ok (degrade to keyword)
    def q_busy_min(sql, params=None): return {"t": 20000}
    check("saturated minute rejects a question",
          minute_capacity_ok(_count_adapter(q_busy_min), num_keys=3, now=noon) is False)

    # single key, 1 in-flight question's worth already used (2500) + 2500 = 5000 vs 0.85*8000=6800 -> OK
    def q_onekey(sql, params=None): return {"t": 2500}
    check("1 key admits a 2nd question in the minute",
          minute_capacity_ok(_count_adapter(q_onekey), num_keys=1, now=noon) is True)
    # 1 key, 3 questions already (7500) + 2500 = 10000 > 6800 -> NOT ok
    def q_onekey_full(sql, params=None): return {"t": 7500}
    check("1 key rejects the 4th question in the minute",
          minute_capacity_ok(_count_adapter(q_onekey_full), num_keys=1, now=noon) is False)

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
