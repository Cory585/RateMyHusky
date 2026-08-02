"""Header-aligned parser for ApplyWeb-era TRACE score XLS reports.

Matches count columns by header suffix ((5.0)..(1.0)) rather than position,
since column offsets differ between the two scale families (Agree/Effective)
and whether an N/A column is present. Never drops a row for answer spread:
xlrd gives blank cells as '' and zeros as 0.0 -- both mean count 0.

The hours-per-week demographic question never appears in sheet 0's Answer Counts
blocks; it is recovered from the per-respondent 'All Responses' sheet instead
(parse_hours_sheet) and appended as a 20th question row, Bluera-style.

Usage:  python scraper/applyweb_pipeline/parse_xls.py --selftest [--require-fixtures]
"""
import os
import sys


class _FakeSheet:
    def __init__(self, grid):
        self.grid = grid
        self.nrows = len(grid)
        self.ncols = max(len(r) for r in grid)

    def cell(self, r, c):
        class _C:
            pass
        cell = _C()
        row = self.grid[r]
        cell.value = row[c] if c < len(row) else ''
        return cell


_RATING_SUFFIX = {"(5.0)": "count_5", "(4.0)": "count_4", "(3.0)": "count_3",
                   "(2.0)": "count_2", "(1.0)": "count_1"}


def _num(v):
    """Numeric cell value or None. xlrd: empty cell -> '', numbers -> float. 0.0 IS a value."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.strip():
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _header_map(cells):
    """Header row -> column map, or None. Match count cols by numeric suffix only."""
    cols = {}
    for c, v in enumerate(cells):
        if not isinstance(v, str):
            continue
        t = v.strip()
        for suf, key in _RATING_SUFFIX.items():
            if t.endswith(suf) and not t.startswith("N/A"):
                cols[key] = c
        if t == "N/A" or t.startswith("N/A ("):
            cols["na"] = c
            cols["na_old"] = (t != "N/A")
        elif t == "Mean":
            cols["mean"] = c
        elif t == "Median":
            cols["median"] = c
        elif t == "Std Dev":
            cols["std_dev"] = c
    if all(k in cols for k in _RATING_SUFFIX.values()) and "mean" in cols:
        return cols
    return None


def _stats(counts):
    """(mean_unrounded, median, std_dev_r2) from counts [c5,c4,c3,c2,c1], N/A excluded. None if N==0."""
    n = sum(counts)
    if n == 0:
        return None, None, None
    mean = sum(r * c for r, c in zip((5, 4, 3, 2, 1), counts)) / n
    vals = sorted(r for r, c in zip((5, 4, 3, 2, 1), counts) for _ in range(c))
    mid = n // 2
    median = float(vals[mid]) if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0
    std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
    return mean, median, round(std, 2)


def parse_sheet(sheet):
    rep = {"enrollment": None, "completed": None, "questions": [], "blocks": 0,
           "summary_rows": 0, "old_vintage": False, "unexpected_rows": [],
           "mean_mismatches": [], "expected_na_mismatches": 0, "duplicate_questions": []}
    seen = set()
    cols = None
    for r in range(sheet.nrows):
        cells = [sheet.cell(r, c).value for c in range(sheet.ncols)]
        hdr = _header_map(cells)
        if hdr is not None:
            cols = hdr
            rep["blocks"] += 1
            if cols.get("na_old"):
                rep["old_vintage"] = True
            continue
        first = cells[0].strip() if isinstance(cells[0], str) else ""
        if cols is None:                       # preamble
            low = first.lower()
            if low.startswith("enrollment") or (low.startswith("completed") and "completion" not in low):
                val = next((int(n) for n in map(_num, cells[1:]) if n is not None), None)
                rep["enrollment" if low.startswith("enrollment") else "completed"] = val
            continue
        count_vals = [_num(cells[cols[k]]) if cols[k] < len(cells) else None
                      for k in ("count_5", "count_4", "count_3", "count_2", "count_1")]
        na_val = _num(cells[cols["na"]]) if "na" in cols and cols["na"] < len(cells) else None
        mean_printed = _num(cells[cols["mean"]]) if cols["mean"] < len(cells) else None
        if not first and all(v is None for v in count_vals + [na_val, mean_printed]):
            continue                            # blank spacer row
        if all(v is None for v in count_vals) and na_val is None and mean_printed is not None:
            rep["summary_rows"] += 1            # summary row (blank counts, printed stats only)
            continue
        if any(v is not None for v in count_vals) or na_val is not None:
            counts = [int(v or 0) for v in count_vals]
            na = int(na_val or 0) if "na" in cols else 0
            mean_u, median, std = _stats(counts)
            if first in seen:
                rep["duplicate_questions"].append(first)
                continue
            seen.add(first)
            if mean_u is not None and mean_printed is not None and abs(mean_u - mean_printed) > 0.01:
                if cols.get("na_old") and na > 0:
                    rep["expected_na_mismatches"] += 1   # old vintage printed mean counts N/A as 0.0
                else:
                    rep["mean_mismatches"].append(first)
            rep["questions"].append({
                "question": first, "count_5": counts[0], "count_4": counts[1],
                "count_3": counts[2], "count_2": counts[3], "count_1": counts[4],
                "count_na": na, "mean": round(mean_u, 2) if mean_u is not None else None,
                "median": median, "std_dev": std})
            continue
        rep["unexpected_rows"].append(first or "<blank>")
    return rep


# count_5="More than 10" .. count_1="0-2", same buckets/midpoints as trace_pipeline/ingest.py
HOURS_MIDPOINTS = {5: 12, 4: 9, 3: 6, 2: 3.5, 1: 1}


def parse_hours_sheet(sheet):
    """Hours-per-week question from an 'All Responses' sheet -> question dict, or None.

    Per-respondent 1..5 codes map to the ascending hour buckets (code semantics pinned
    by the Likert columns, whose codes tally exactly to the summary sheet's
    (1.0)..(5.0) counts). Mean is the Bluera midpoint mean -- backend/precompute.py
    preserves it for hours questions instead of recomputing a 1-5 mean."""
    for r in range(sheet.nrows):
        for c in range(sheet.ncols):
            v = sheet.cell(r, c).value
            if not (isinstance(v, str) and "hours per week" in v.lower()):
                continue
            tally = {k: 0 for k in (1, 2, 3, 4, 5)}
            for rr in range(r + 1, sheet.nrows):
                f = _num(sheet.cell(rr, c).value)
                if f is not None and f in tally:
                    tally[f] += 1
            n = sum(tally.values())
            if n == 0:
                return None
            mean = round(sum(HOURS_MIDPOINTS[k] * tally[k] for k in tally) / n, 2)
            return {"question": v.strip(),
                    "count_5": tally[5], "count_4": tally[4], "count_3": tally[3],
                    "count_2": tally[2], "count_1": tally[1], "count_na": 0,
                    "mean": mean, "median": None, "std_dev": None}
    return None


def parse_report(xls_bytes):
    import xlrd
    wb = xlrd.open_workbook(file_contents=xls_bytes)
    rep = parse_sheet(wb.sheet_by_index(0))
    for si in range(1, wb.nsheets):
        hours = parse_hours_sheet(wb.sheet_by_index(si))
        if hours is not None:
            rep["questions"].append(hours)
            break
    return rep


def report_ok(rep):
    return (rep["enrollment"] is not None and rep["completed"] is not None
            and len(rep["questions"]) > 0 and not rep["unexpected_rows"]
            and not rep["mean_mismatches"] and not rep["duplicate_questions"])


AGREE = ["", "Strongly Agree (5.0)", "Agree (4.0)", "Neutral (3.0)", "Disagree (2.0)",
         "Strongly Disagree (1.0)", "Mean", "Median", "Std Dev", "Response Rate"]
EFFECT = ["", "Almost Always Effective (5.0)", "Usually Effective (4.0)", "Sometimes Effective (3.0)",
          "Rarely Effective (2.0)", "Never Effective (1.0)", "Mean", "Median", "Std Dev", "Response Rate"]
NA_NEW = ["", "Strongly Agree (5.0)", "Agree (4.0)", "Neutral (3.0)", "Disagree (2.0)",
          "Strongly Disagree (1.0)", "N/A", "Mean", "Median", "Std Dev", "Response Rate"]
NA_OLD = ["", "Strongly Agree (5.0)", "Agree (4.0)", "Neutral (3.0)", "Disagree (2.0)",
          "Strongly Disagree (1.0)", "N/A (0.0)", "Mean", "Median", "Std Dev", "Response Rate"]
PRE = [["Northeastern University Course Evaluations"], [], ["PHLS 1101, Section I04"],
       ["Enrollment:", 22.0], ["Completed:", 15.0], ["Answer Counts"]]


HOURS_Q = ("The number of hours per week I devoted to this course outside "
           "scheduled class meeting times.")

# (question, c5, c4, c3, c2, c1, na, mean, median, std_dev) — mean/median/std_dev exclude N/A;
# the trailing hours row comes from the All Responses sheet (midpoint mean, no median/std_dev)
EXPECTED_1 = [  # Spring 2025 PHLS1101 I04 — new vintage; enrollment 22, completed 15
    ('Online course materials', 13, 0, 0, 0, 0, 1, 5.0, 5.0, 0.0),
    ('Online Interactions', 13, 0, 0, 0, 0, 1, 5.0, 5.0, 0.0),
    ('Sense of community', 13, 0, 0, 0, 0, 1, 5.0, 5.0, 0.0),
    ('Computer skills', 12, 0, 0, 1, 0, 1, 4.77, 5.0, 0.8),
    ('Syllabus', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Course Materials', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('In-class Sessions', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Out-of-class', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Challenging', 11, 2, 1, 0, 0, 0, 4.71, 5.0, 0.59),
    ('Learned a lot', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Prepared', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Effective time', 13, 0, 1, 0, 0, 0, 4.86, 5.0, 0.52),
    ('Clear communication', 13, 1, 0, 0, 0, 0, 4.93, 5.0, 0.26),
    ('Feedback', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Fairly evaluated', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Outside assist', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Respect', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Enthusiasm', 14, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Overall rating of teaching', 15, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    (HOURS_Q, 3, 3, 2, 6, 1, 0, 6.47, None, None),
]
EXPECTED_2 = [  # Fall 2021 SCLY1210 I02 — OLD vintage; enrollment 28, completed 18
    ('Online course materials', 12, 2, 1, 0, 0, 3, 4.73, 5.0, 0.57),
    ('Online Interactions', 12, 2, 1, 0, 0, 3, 4.73, 5.0, 0.57),
    ('Sense of community', 12, 1, 1, 1, 0, 3, 4.6, 5.0, 0.88),
    ('Computer skills', 9, 3, 3, 0, 0, 3, 4.4, 5.0, 0.8),
    ('Syllabus', 15, 2, 1, 0, 0, 0, 4.78, 5.0, 0.53),
    ('Course Materials', 15, 2, 1, 0, 0, 0, 4.78, 5.0, 0.53),
    ('In-class Sessions', 14, 3, 1, 0, 0, 0, 4.72, 5.0, 0.56),
    ('Out-of-class', 14, 2, 2, 0, 0, 0, 4.67, 5.0, 0.67),
    ('Challenging', 10, 3, 5, 0, 0, 0, 4.28, 5.0, 0.87),
    ('Learned a lot', 14, 2, 2, 0, 0, 0, 4.67, 5.0, 0.67),
    ('Prepared', 16, 1, 1, 0, 0, 0, 4.83, 5.0, 0.5),
    ('Effective time', 16, 1, 1, 0, 0, 0, 4.83, 5.0, 0.5),
    ('Clear communication', 16, 1, 1, 0, 0, 0, 4.83, 5.0, 0.5),
    ('Feedback', 15, 2, 1, 0, 0, 0, 4.78, 5.0, 0.53),
    ('Fairly evaluated', 14, 3, 1, 0, 0, 0, 4.72, 5.0, 0.56),
    ('Outside assist', 14, 2, 2, 0, 0, 0, 4.67, 5.0, 0.67),
    ('Respect', 15, 2, 1, 0, 0, 0, 4.78, 5.0, 0.53),
    ('Enthusiasm', 16, 1, 1, 0, 0, 0, 4.83, 5.0, 0.5),
    ('Overall rating of teaching', 13, 3, 2, 0, 0, 0, 4.61, 5.0, 0.68),
    (HOURS_Q, 1, 4, 6, 5, 2, 0, 5.75, None, None),
]
EXPECTED_3 = [  # Fall 2024 SCLY1210 I01 — new vintage; enrollment 19, completed 16
    ('Online course materials', 12, 3, 0, 0, 0, 1, 4.8, 5.0, 0.4),
    ('Online Interactions', 11, 3, 0, 0, 0, 2, 4.79, 5.0, 0.41),
    ('Sense of community', 12, 2, 0, 0, 0, 2, 4.86, 5.0, 0.35),
    ('Computer skills', 12, 2, 0, 0, 1, 1, 4.6, 5.0, 1.02),
    ('Syllabus', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Course Materials', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('In-class Sessions', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Out-of-class', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Challenging', 10, 4, 2, 0, 0, 0, 4.5, 5.0, 0.71),
    ('Learned a lot', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Prepared', 13, 3, 0, 0, 0, 0, 4.81, 5.0, 0.39),
    ('Effective time', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Clear communication', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Feedback', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Fairly evaluated', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Outside assist', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Respect', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Enthusiasm', 14, 2, 0, 0, 0, 0, 4.88, 5.0, 0.33),
    ('Overall rating of teaching', 15, 1, 0, 0, 0, 0, 4.94, 5.0, 0.24),
    (HOURS_Q, 1, 3, 2, 10, 0, 0, 5.38, None, None),
]
EXPECTED_4 = [  # Spring 2022 PHLS1145 I01 — OLD vintage; enrollment 18, completed 12
    ('Online course materials', 8, 3, 0, 0, 0, 1, 4.73, 5.0, 0.45),
    ('Online Interactions', 8, 3, 0, 0, 0, 1, 4.73, 5.0, 0.45),
    ('Sense of community', 8, 2, 0, 0, 0, 2, 4.8, 5.0, 0.4),
    ('Computer skills', 8, 3, 0, 0, 0, 1, 4.73, 5.0, 0.45),
    ('Syllabus', 10, 1, 1, 0, 0, 0, 4.75, 5.0, 0.6),
    ('Course Materials', 10, 2, 0, 0, 0, 0, 4.83, 5.0, 0.37),
    ('In-class Sessions', 11, 1, 0, 0, 0, 0, 4.92, 5.0, 0.28),
    ('Out-of-class', 9, 3, 0, 0, 0, 0, 4.75, 5.0, 0.43),
    ('Challenging', 7, 1, 4, 0, 0, 0, 4.25, 5.0, 0.92),
    ('Learned a lot', 9, 3, 0, 0, 0, 0, 4.75, 5.0, 0.43),
    ('Prepared', 12, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Effective time', 12, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Clear communication', 12, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Feedback', 11, 1, 0, 0, 0, 0, 4.92, 5.0, 0.28),
    ('Fairly evaluated', 11, 1, 0, 0, 0, 0, 4.92, 5.0, 0.28),
    ('Outside assist', 10, 2, 0, 0, 0, 0, 4.83, 5.0, 0.37),
    ('Respect', 11, 1, 0, 0, 0, 0, 4.92, 5.0, 0.28),
    ('Enthusiasm', 12, 0, 0, 0, 0, 0, 5.0, 5.0, 0.0),
    ('Overall rating of teaching', 11, 1, 0, 0, 0, 0, 4.92, 5.0, 0.28),
    (HOURS_Q, 1, 2, 5, 4, 0, 0, 6.17, None, None),
]
FIXTURE_META = {
    "quantitative_report_1.xls": {"expected": EXPECTED_1, "enrollment": 22, "completed": 15,
        "old_vintage": False, "expected_na_mismatches": 0, "triple": (102980, 87, 196)},
    "quantitative_report_2.xls": {"expected": EXPECTED_2, "enrollment": 28, "completed": 18,
        "old_vintage": True, "expected_na_mismatches": 4, "triple": (64329, 87, 145)},
    "quantitative_report_3.xls": {"expected": EXPECTED_3, "enrollment": 19, "completed": 16,
        "old_vintage": False, "expected_na_mismatches": 0, "triple": (97591, 87, 192)},
    "quantitative_report_4.xls": {"expected": EXPECTED_4, "enrollment": 18, "completed": 12,
        "old_vintage": True, "expected_na_mismatches": 4, "triple": (68797, 87, 148)},
}


def run_fixture_gate(fixtures_dir=None, verbose=True):
    import common  # same-dir import, like trace_pipeline modules
    fixtures_dir = fixtures_dir or common.FIXTURES_DIR
    ok_all = True
    for fname, meta in FIXTURE_META.items():
        path = os.path.join(fixtures_dir, fname)
        rep = parse_report(open(path, "rb").read())
        errs = []
        if rep["enrollment"] != meta["enrollment"]: errs.append("enrollment")
        if rep["completed"] != meta["completed"]: errs.append("completed")
        if rep["old_vintage"] != meta["old_vintage"]: errs.append("vintage")
        if rep["expected_na_mismatches"] != meta["expected_na_mismatches"]: errs.append("na_mismatch_count")
        if rep["blocks"] != 3: errs.append(f"blocks={rep['blocks']}")
        if rep["summary_rows"] != 5: errs.append(f"summaries={rep['summary_rows']}")
        if not report_ok(rep): errs.append(f"not ok: {rep['unexpected_rows']}{rep['mean_mismatches']}{rep['duplicate_questions']}")
        got = [(q["question"], q["count_5"], q["count_4"], q["count_3"], q["count_2"], q["count_1"],
                q["count_na"], q["mean"], q["median"], q["std_dev"]) for q in rep["questions"]]
        if got != meta["expected"]:
            for g, e in zip(got, meta["expected"]):
                if g != e: errs.append(f"row {e[0]!r}: got {g}")
            if len(got) != len(meta["expected"]): errs.append(f"row count {len(got)} != {len(meta['expected'])}")
        status = "PASS" if not errs else "FAIL"
        if verbose: print(f"{status}: fixture gate {fname}" + ("" if not errs else f" — {errs}"))
        ok_all &= not errs
    return ok_all


def selftest(require_fixtures=False):
    fails = []

    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    # 1. basic agree block: counts, zeros-as-0.0 and blanks both -> 0, recomputed stats
    rep = parse_sheet(_FakeSheet(PRE + [AGREE,
        ["Syllabus", 14.0, 0.0, '', 0.0, 0.0, 5.0, 5.0, 0.0, 0.64],
        ["Challenging", 11.0, 2.0, 1.0, 0.0, 0.0, 4.714, 5.0, 0.59, 0.64],
        ["Course summary", '', '', '', '', '', 4.9, 5.0, 0.3, 0.64]]))
    check("enrollment/completed", rep["enrollment"] == 22 and rep["completed"] == 15)
    check("two questions kept", [q["question"] for q in rep["questions"]] == ["Syllabus", "Challenging"])
    q0, q1 = rep["questions"]
    check("unanimous row kept with zeros", (q0["count_5"], q0["count_4"], q0["count_3"], q0["count_2"], q0["count_1"]) == (14, 0, 0, 0, 0))
    check("unanimous mean 5.0", q0["mean"] == 5.0 and q0["median"] == 5.0 and q0["std_dev"] == 0.0)
    check("recomputed mean r2", q1["mean"] == 4.71)          # 66/14
    check("population std dev", q1["std_dev"] == 0.59)
    check("summary row skipped+counted", rep["summary_rows"] == 1)
    check("no na col -> count_na 0", q0["count_na"] == 0)
    check("one block", rep["blocks"] == 1)
    check("clean report ok", report_ok(rep))

    # 2. new-vintage N/A: excluded from stats, cross-check passes
    rep = parse_sheet(_FakeSheet(PRE + [NA_NEW,
        ["Computer skills", 12.0, 0.0, 0.0, 1.0, 0.0, 1.0, 4.769, 5.0, 0.8, 0.64]]))
    q = rep["questions"][0]
    check("count_na captured", q["count_na"] == 1)
    check("na excluded from mean", q["mean"] == 4.77)        # 62/13
    check("new vintage flag", rep["old_vintage"] is False)
    check("printed-mean crosscheck passes", rep["mean_mismatches"] == [] and rep["expected_na_mismatches"] == 0)

    # 3. old-vintage N/A: printed mean includes N/A as 0 -> expected mismatch, not an error
    rep = parse_sheet(_FakeSheet(PRE + [NA_OLD,
        ["Online course materials", 12.0, 2.0, 1.0, 0.0, 0.0, 3.0, 3.944, 5.0, 1.84, 0.64]]))
    q = rep["questions"][0]
    check("old vintage flag", rep["old_vintage"] is True)
    check("old vintage mean excludes na", q["mean"] == 4.73)  # 71/15
    check("expected na mismatch counted", rep["expected_na_mismatches"] == 1)
    check("not flagged as error", rep["mean_mismatches"] == [] and report_ok(rep))

    # 4. all-N/A row -> stats None
    rep = parse_sheet(_FakeSheet(PRE + [NA_NEW, ["Sense of community", '', '', '', '', '', 15.0, '', '', '', '']]))
    q = rep["questions"][0]
    check("all-na row kept", q["count_na"] == 15 and (q["count_5"], q["count_1"]) == (0, 0))
    check("all-na stats None", q["mean"] is None and q["median"] is None and q["std_dev"] is None)

    # 5. effectiveness scale family maps by suffix
    rep = parse_sheet(_FakeSheet(PRE + [EFFECT, ["Overall rating of teaching", 15.0, 0.0, 0.0, 0.0, 0.0, 5.0, 5.0, 0.0, 0.68]]))
    check("effect family parsed", rep["questions"][0]["mean"] == 5.0)

    # 6. even-N median can be x.5
    rep = parse_sheet(_FakeSheet(PRE + [AGREE, ["Q", 1.0, 1.0, 0.0, 0.0, 0.0, 4.5, 4.5, 0.5, 0.1]]))
    check("even median 4.5", rep["questions"][0]["median"] == 4.5)

    # 7. genuine printed-mean disagreement -> mean_mismatches, report not ok
    rep = parse_sheet(_FakeSheet(PRE + [AGREE, ["Q", 10.0, 0.0, 0.0, 0.0, 0.0, 3.2, 5.0, 0.0, 0.5]]))
    check("mean mismatch flagged", rep["mean_mismatches"] == ["Q"] and not report_ok(rep))

    # 8. junk row under a header -> unexpected_rows, report not ok
    rep = parse_sheet(_FakeSheet(PRE + [AGREE, ["Some stray text", '', 'huh', '', '', '', '', '', '', '']]))
    check("unexpected row flagged", rep["unexpected_rows"] == ["Some stray text"] and not report_ok(rep))

    # 9. duplicate question label -> flagged
    rep = parse_sheet(_FakeSheet(PRE + [AGREE,
        ["Syllabus", 14.0, 0.0, 0.0, 0.0, 0.0, 5.0, 5.0, 0.0, 0.64],
        ["Syllabus", 10.0, 0.0, 0.0, 0.0, 0.0, 5.0, 5.0, 0.0, 0.64]]))
    check("duplicate question flagged", rep["duplicate_questions"] == ["Syllabus"] and not report_ok(rep))

    # 10. missing enrollment/completed or zero questions -> not ok
    rep = parse_sheet(_FakeSheet([["Title"], AGREE]))
    check("no enrollment/questions -> not ok", not report_ok(rep))

    # 11. hours-per-week from the All Responses sheet: 1..5 codes tallied into buckets
    #     (1=0-2h .. 5=More than 10h), Bluera midpoint mean, blanks/junk/out-of-range ignored
    hours_grid = [["Northeastern University Online Course Evaluations"], [],
                  ["All Student Responses:"],
                  ["", "The syllabus was accurate.", HOURS_Q],
                  ["Eval #1", 5.0, 2.0],
                  ["Eval #2", 5.0, 2.0],
                  ["Eval #3", 4.0, 5.0],
                  ["Eval #4", 5.0, ''],       # skipped the hours question
                  ["Eval #5", 5.0, "junk"],
                  ["Eval #6", 5.0, 7.0]]      # out-of-range code
    q = parse_hours_sheet(_FakeSheet(hours_grid))
    check("hours question text from header", q["question"] == HOURS_Q)
    check("hours codes -> bucket counts",
          (q["count_5"], q["count_4"], q["count_3"], q["count_2"], q["count_1"]) == (1, 0, 0, 2, 0))
    check("hours midpoint mean", q["mean"] == 6.33)          # (12 + 2*3.5) / 3
    check("hours row shape", q["count_na"] == 0 and q["median"] is None and q["std_dev"] is None)
    check("hours header absent -> None",
          parse_hours_sheet(_FakeSheet([["nothing here"], ["Eval #1", 5.0]])) is None)
    check("hours all blank -> None",
          parse_hours_sheet(_FakeSheet([["", HOURS_Q], ["Eval #1", '']])) is None)

    # 12. four-fixture byte-exact gate against real XLS files
    import common  # same-dir import, like trace_pipeline modules
    if os.path.isdir(common.FIXTURES_DIR):
        if not run_fixture_gate():
            fails.append("fixture gate")
    else:
        print("WARN: fixtures missing — gate skipped")
        if require_fixtures:
            fails.append("fixtures missing")

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest(require_fixtures="--require-fixtures" in sys.argv))
    print("Import-only module. Use --selftest.")
