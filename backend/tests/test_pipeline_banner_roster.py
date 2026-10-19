from pipeline import banner_roster as br

def bci(key, course, name, method="no_match", slug=None, terms=1, last="202630"):
    return {"subject_course": course, "instructor_key": key, "instructor_name": name,
            "professor_slug": slug, "match_method": method, "terms_taught": terms,
            "last_term_code": last}

PROFS = [{"slug": "john-smith", "name": "John Smith", "name_key": "john smith"},
         {"slug": "ada-park", "name": "Ada Park", "name_key": "ada park"}]
DEPTS = {"CS": "Computer Science", "MATH": "Mathematics"}
never = lambda key: False


def test_no_match_instructor_becomes_a_banner_professor():
    rows, counts = br.resolve_banner_professors(
        [bci("lucia nunez", "CS3100", "Nunez, Lucia", terms=3),
         bci("lucia nunez", "MATH1365", "Nunez, Lucia", terms=1)], PROFS, DEPTS, never)
    assert rows == [{"slug": "lucia-nunez", "name": "Lucia Nunez", "name_key": "lucia nunez",
                     "department": "Computer Science", "college": br.get_college("Computer Science"),
                     "image_url": None, "focus_x": None, "focus_y": None, "origin": "banner"}]
    assert counts["created"] == 1


def test_only_no_match_rows_create_professors():
    rows, _ = br.resolve_banner_professors(
        [bci("wei wang", "CHEM1211", "Wang, Wei", method=m)
         for m in ("ambiguous", "subject_mismatch", "alias_unverified", "roster_duplicate")],
        PROFS, DEPTS, never)
    assert rows == []


def test_a_key_mixing_no_match_with_another_verdict_is_skipped():
    rows, counts = br.resolve_banner_professors(
        [bci("wei wang", "CHEM1211", "Wang, Wei"),
         bci("wei wang", "CHEM2311", "Wang, Wei", method="roster_duplicate")], PROFS, DEPTS, never)
    assert rows == [] and counts["mixed"] == 1


def test_existing_name_key_is_known_not_created():
    rows, counts = br.resolve_banner_professors(
        [bci("ada park", "CS2500", "Park, Ada")], PROFS, DEPTS, never)
    assert rows == [] and counts["known"] == 1


def test_middle_name_matching_first_and_last_is_a_possible_duplicate():
    rows, counts = br.resolve_banner_professors(
        [bci("john a smith", "CS2500", "Smith, John A")], PROFS, DEPTS, never)
    assert rows == [] and counts["possible_duplicate"] == 1


def test_denied_names_are_never_created():
    rows, counts = br.resolve_banner_professors(
        [bci("lucia nunez", "CS3100", "Nunez, Lucia")], PROFS, DEPTS, lambda k: k == "lucia nunez")
    assert rows == [] and counts["denied"] == 1


def test_slug_collision_gets_a_suffix():
    profs = PROFS + [{"slug": "lucia-nunez", "name": "Lucia Nunez", "name_key": "lucia nunez x"}]
    rows, _ = br.resolve_banner_professors([bci("lucia nunez", "CS3100", "Nunez, Lucia")],
                                           profs, DEPTS, never)
    assert rows[0]["slug"] == "lucia-nunez-2"


def test_newest_spelling_names_the_professor():
    rows, _ = br.resolve_banner_professors(
        [bci("lucia nunez", "CS3100", "Nunez, L", last="202530"),
         bci("lucia nunez", "CS3200", "Nunez, Lucia", last="202710")], PROFS, DEPTS, never)
    assert rows[0]["name"] == "Lucia Nunez"


def test_unknown_subject_leaves_department_empty():
    rows, _ = br.resolve_banner_professors([bci("lucia nunez", "ZZZZ1000", "Nunez, Lucia")],
                                           PROFS, DEPTS, never)
    assert rows[0]["department"] is None and rows[0]["college"] == br.get_college(None)


def test_rerun_after_insert_creates_nothing():
    first, _ = br.resolve_banner_professors([bci("lucia nunez", "CS3100", "Nunez, Lucia")],
                                            PROFS, DEPTS, never)
    again, counts = br.resolve_banner_professors([bci("lucia nunez", "CS3100", "Nunez, Lucia")],
                                                 PROFS + first, DEPTS, never)
    assert again == [] and counts["known"] == 1


def test_subject_departments_takes_the_majority_and_ignores_unmatched():
    rows = [bci("a", "CS2500", "A", slug="p1"), bci("b", "CS3500", "B", slug="p2"),
            bci("c", "CS1800", "C", slug="p3"), bci("d", "CS9999", "D")]
    depts = {"p1": "Computer Science", "p2": "Computer Science", "p3": "Mathematics"}
    assert br.subject_departments(rows, depts) == {"CS": "Computer Science"}


def test_banner_key_without_middle_name_is_a_duplicate_of_a_middle_name_professor():
    profs = PROFS + [{"slug": "john-a-smith", "name": "John A Smith", "name_key": "john a smith"}]
    rows, counts = br.resolve_banner_professors(
        [bci("john smith", "CS2500", "Smith, John")], profs[2:], DEPTS, never)
    assert rows == [] and counts["possible_duplicate"] == 1


def test_two_banner_only_keys_differing_by_middle_name_create_one_page():
    rows, counts = br.resolve_banner_professors(
        [bci("john a smith", "CS2500", "Smith, John A"), bci("john smith", "CS2500", "Smith, John")],
        [], DEPTS, never)
    assert [r["name_key"] for r in rows] == ["john a smith"]
    assert counts["created"] == 1 and counts["possible_duplicate"] == 1


def test_fully_matched_key_is_matched_not_mixed():
    rows, counts = br.resolve_banner_professors(
        [bci("wei wang", "CHEM1211", "Wang, Wei", method="ambiguous")], PROFS, DEPTS, never)
    assert rows == [] and counts["matched"] == 1 and counts["mixed"] == 0
