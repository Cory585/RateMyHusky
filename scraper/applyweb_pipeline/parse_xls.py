"""Header-aligned parser for ApplyWeb-era TRACE score XLS reports.

Matches count columns by header suffix ((5.0)..(1.0)) rather than position,
since column offsets differ between the two scale families (Agree/Effective)
and whether an N/A column is present. Never drops a row for answer spread:
xlrd gives blank cells as '' and zeros as 0.0 -- both mean count 0.

Usage:  python scraper/applyweb_pipeline/parse_xls.py --selftest
"""
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


def parse_report(xls_bytes):
    import xlrd
    wb = xlrd.open_workbook(file_contents=xls_bytes)
    return parse_sheet(wb.sheet_by_index(0))


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


def selftest():
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

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("Import-only module. Use --selftest.")
