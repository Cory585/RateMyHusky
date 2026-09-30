"""Course codes on rmp_reviews and the course_catalog built from them.

No database: every function under test is pure.
"""

import pytest

from precompute import (
    build_course_catalog_rows,
    normalize_course_code,
    resolve_course_codes,
)


# ── normalize_course_code ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw,code", [
    ("CS2500", "CS2500"),
    ("cs2500", "CS2500"),
    ("cs 2500", "CS2500"),
    ("CS-2500", "CS2500"),
    (" MGSC 2301 ", "MGSC2301"),
])
def test_codes_that_resolve(raw, code):
    assert normalize_course_code(raw) == code


@pytest.mark.parametrize("raw", [
    None, float("nan"), "", "DUEPROCESS", "ECON115", "MISM", "2301", "CS25000",
    "GE1501AND1502", "1234 CS", "ACCOUNTING2301", "BEFORE1400", "C1100",
])
def test_text_that_is_not_a_code(raw):
    assert normalize_course_code(raw) is None


# ── resolve_course_codes: the bare-number rule ──────────────────────────────

def test_a_bare_number_takes_the_subject_the_professor_also_used():
    codes = resolve_course_codes(["a b", "a b"], ["2301", "SCHM2301"])
    assert codes == ["SCHM2301", "SCHM2301"]


def test_a_bare_number_with_two_subjects_stays_unresolved():
    codes = resolve_course_codes(["a b"] * 3, ["2301", "MGSC2301", "SCHM2301"])
    assert codes == [None, "MGSC2301", "SCHM2301"]


def test_a_bare_number_with_no_matching_number_stays_unresolved():
    codes = resolve_course_codes(["a b", "a b"], ["2301", "MGSC2302"])
    assert codes == [None, "MGSC2302"]


def test_a_bare_number_does_not_borrow_another_professors_subject():
    codes = resolve_course_codes(["a b", "c d"], ["2301", "MGSC2301"])
    assert codes == [None, "MGSC2301"]


def test_the_same_subject_written_two_ways_is_one_subject():
    codes = resolve_course_codes(["a b"] * 3, ["2301", "mgsc2301", "MGSC 2301"])
    assert codes == ["MGSC2301"] * 3


def test_a_bare_number_never_resolves_from_another_bare_number():
    assert resolve_course_codes(["a b", "a b"], ["2301", "2301"]) == [None, None]


def test_results_stay_aligned_with_the_inputs():
    codes = resolve_course_codes(["a", "b", "c"], [None, "CS2500", "junk"])
    assert codes == [None, "CS2500", None]


def test_empty_input():
    assert resolve_course_codes([], []) == []


# ── build_course_catalog_rows ───────────────────────────────────────────────

def _rows(records, catalog=None, previous=None):
    return {r[0]: r for r in build_course_catalog_rows(records, catalog or {}, previous or {})}


def test_one_row_per_code_with_mean_quality_and_count():
    rows = build_course_catalog_rows(
        [("CS2500", 5, "Computer Science"), ("CS2500", 3, "Computer Science"),
         ("ENGW1111", 4, "English")], {}, {})
    by = {r[0]: r for r in rows}
    assert sorted(by) == ["CS2500", "ENGW1111"]
    assert by["CS2500"][4] == 4.0 and by["CS2500"][5] == 2
    assert by["ENGW1111"][4] == 4.0 and by["ENGW1111"][5] == 1


def test_row_width_matches_the_insert_columns():
    (row,) = build_course_catalog_rows([("CS2500", 5, "X")], {}, {})
    # code, name, department, search_text, avg_rating, num_ratings
    assert len(row) == 6


def test_out_of_range_quality_is_counted_but_not_averaged():
    row = _rows([("CS2500", 4, "X"), ("CS2500", 0, "X"), ("CS2500", None, "X")])["CS2500"]
    assert row[4] == 4.0 and row[5] == 3


def test_a_code_with_no_usable_quality_has_no_rating():
    assert _rows([("CS2500", None, "X")])["CS2500"][4] is None


def test_name_and_department_come_from_catalog_courses_first():
    rows = _rows([("CS2500", 5, "RMP Dept")],
                 catalog={"CS2500": ("Fundamentals of Computer Science 1", "Khoury")},
                 previous={"CS2500": ("Old Name", "Old Dept")})
    assert rows["CS2500"][1:3] == ("Fundamentals of Computer Science 1", "Khoury")


def test_previous_course_catalog_carries_over_when_not_in_catalog_courses():
    rows = _rows([("CS2500", 5, "RMP Dept")],
                 catalog={"OTHER1000": ("x", "y")},
                 previous={"CS2500": ("Old Name", "Old Dept")})
    assert rows["CS2500"][1:3] == ("Old Name", "Old Dept")


def test_fallback_is_null_name_and_most_common_review_department():
    rows = _rows([("CS2500", 5, "Math"), ("CS2500", 4, "Computer Science"),
                  ("CS2500", 3, "Computer Science")])
    assert rows["CS2500"][1] is None
    assert rows["CS2500"][2] == "Computer Science"


def test_a_known_name_with_a_blank_department_falls_back_for_the_department():
    rows = _rows([("CS2500", 5, "Computer Science")], previous={"CS2500": ("Name", "")})
    assert rows["CS2500"][1:3] == ("Name", "Computer Science")


def test_nan_department_never_reaches_the_page():
    rows = _rows([("CS2500", 5, float("nan"))])
    assert rows["CS2500"][2] == ""


def test_search_text_is_code_and_name_lowercased():
    rows = _rows([("ME2350", 5, "X")], catalog={"ME2350": ("Statics", "Eng")})
    assert rows["ME2350"][3] == "me2350 statics"


def test_search_text_without_a_name_is_just_the_code():
    assert _rows([("ME2350", 5, "X")])["ME2350"][3] == "me2350"


def test_unresolved_reviews_are_skipped():
    rows = build_course_catalog_rows([(None, 5, "X"), ("", 5, "X")], {}, {})
    assert rows == []
