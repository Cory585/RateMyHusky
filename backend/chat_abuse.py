import sys, argparse

STRIKE_STATUSES = ("injection_blocked", "off_topic", "defamation_framing")
_WARN = "I can only answer questions about Northeastern professors and courses."
_CAP_MSG = "You've hit today's question limit. Try again tomorrow, or use keyword search."
_BAN_MSG = ("Your access to the question feature has been suspended for repeated misuse. "
            "You can appeal through the feedback form.")
# strike count -> that day's question cap (strikes 1-2 = warn-only; 6+ = ban, handled separately)
_CAPS = {3: 5, 4: 3, 5: 1}

def strike_count(session_token, query_one_fn):
    row = query_one_fn(
        "SELECT count(*) AS c FROM ask_log "
        "WHERE session_token = %s AND result_status = ANY(%s)",
        (session_token, list(STRIKE_STATUSES)))
    return (row or {}).get("c", 0) or 0

def daily_question_count(session_token, query_one_fn):
    row = query_one_fn(
        "SELECT count(*) AS c FROM ask_log "
        "WHERE session_token = %s AND mode = 'question' "
        "AND date_trunc('day', created_at) = current_date",
        (session_token,))
    return (row or {}).get("c", 0) or 0

def abuse_check(session_token, query_one_fn):
    strikes = strike_count(session_token, query_one_fn)
    if strikes >= 6:
        return {"allowed": False, "banned": True, "message": _BAN_MSG}
    if strikes >= 3:
        cap = _CAPS[min(strikes, 5)]
        used = daily_question_count(session_token, query_one_fn)
        if used >= cap:
            return {"allowed": False, "banned": False, "message": _CAP_MSG}
        return {"allowed": True, "banned": False, "message": None}
    if strikes >= 1:
        return {"allowed": True, "banned": False, "message": _WARN}
    return {"allowed": True, "banned": False, "message": None}

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    def make_q(strikes, today):
        def q(sql, params=None):
            return {"c": strikes} if "result_status = ANY" in sql else {"c": today}
        return q

    r0 = abuse_check("s", make_q(0, 0))
    check("no strikes allowed", r0["allowed"] is True and r0["banned"] is False)

    r1 = abuse_check("s", make_q(1, 0))
    check("first strike warns but allows", r1["allowed"] is True and r1["message"])

    r2 = abuse_check("s", make_q(2, 0))   # still in the 1-2 warn tier
    check("second strike warns but allows", r2["allowed"] is True and r2["banned"] is False and r2["message"])

    r3_under = abuse_check("s", make_q(3, 4))   # cap 5/day, used 4 → allowed
    check("3rd strike under 5/day allowed", r3_under["allowed"] is True)
    r3_over = abuse_check("s", make_q(3, 5))    # cap 5/day, used 5 → denied
    check("3rd strike at 5/day denied", r3_over["allowed"] is False and r3_over["banned"] is False)

    r4_under = abuse_check("s", make_q(4, 2))   # cap 3/day, used 2 → allowed
    check("4th strike under 3/day allowed", r4_under["allowed"] is True)

    r5_over = abuse_check("s", make_q(5, 1))    # cap 1/day, used 1 → denied
    check("5th strike at 1/day denied", r5_over["allowed"] is False)

    r6 = abuse_check("s", make_q(6, 0))
    check("6th strike banned with appeal", r6["banned"] is True and "feedback" in r6["message"].lower())

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Graduated abuse ladder (session-keyed).")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
