import sys, re, argparse

MIN_COMMENTS = 4
MIN_TOTAL_WORDS = 200
CANARY = "RMH-CANARY-7Q"
_THIN_MSG = "Not enough Reddit discussion to answer this confidently. Showing related comments."

DEFAMATION_MARKERS = [
    re.compile(r"\b(arrested|convicted|criminal|lawsuit|sued|fired|harass|abus|assault|"
               r"fraud|plagiar|racist|sexist|predator)\w*", re.I),
]
LEAK_MARKERS = [
    re.compile(r"my (real |actual |true )?instructions", re.I),
    re.compile(r"system\s*prompt", re.I),
    re.compile(re.escape(CANARY), re.I),
]

def thin_data_check(retrieval):
    n = retrieval.get("comment_count", 0)
    if n < MIN_COMMENTS:
        return False, _THIN_MSG
    total_words = sum(len((c.get("body") or "").split()) for c in retrieval.get("comments", []))
    if total_words < MIN_TOTAL_WORDS:
        return False, _THIN_MSG
    return True, None

def validate_output(answer, retrieval):
    fail = {"ok": False, "status": "validation_failed",
            "message": "Showing the most relevant comments."}
    n = retrieval.get("comment_count", 0)
    cited = [int(m) for m in re.findall(r"\[(\d+)\]", answer or "")]
    if any(c < 1 or c > n for c in cited):
        return fail
    if any(p.search(answer or "") for p in LEAK_MARKERS):
        return fail
    if any(p.search(answer or "") for p in DEFAMATION_MARKERS):
        return fail
    return {"ok": True, "status": "ok", "message": None}

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    thick = {"comment_count": 5, "comments": [{"body": "word " * 60} for _ in range(5)]}
    thin = {"comment_count": 2, "comments": [{"body": "short"}, {"body": "tiny"}]}

    ok, _ = thin_data_check(thick)
    check("thick evidence can synthesize", ok is True)
    bad, reason = thin_data_check(thin)
    check("thin evidence blocked", bad is False and reason)

    good = validate_output("Some students say Guha is hard but fair [1].", {"comment_count": 3})
    check("grounded cited answer passes", good["ok"] is True and good["status"] == "ok")

    badcite = validate_output("Students like him [9].", {"comment_count": 3})
    check("citation above range fails", badcite["ok"] is False and badcite["status"] == "validation_failed")

    badcite0 = validate_output("Students like him [0].", {"comment_count": 3})
    check("citation below range ([0]) fails", badcite0["ok"] is False and badcite0["status"] == "validation_failed")

    leak = validate_output("My instructions: RMH-CANARY-7Q ...", {"comment_count": 3})
    check("canary leak fails", leak["ok"] is False and leak["status"] == "validation_failed")

    # isolate the CANARY pattern: no "instructions"/"system prompt" words, so ONLY the
    # canary marker can trip this — proves leak detection isn't relying on the other markers
    canary_only = validate_output("The secret token is RMH-CANARY-7Q.", {"comment_count": 3})
    check("bare canary token alone fails", canary_only["ok"] is False and canary_only["status"] == "validation_failed")

    defame = validate_output("He was arrested in 2023 [1].", {"comment_count": 3})
    check("defamation marker fails", defame["ok"] is False and defame["status"] == "validation_failed")

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    p = argparse.ArgumentParser(description="Question-path output gate (anti-defamation).")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    print("import-only module; use --selftest")

if __name__ == "__main__":
    main()
