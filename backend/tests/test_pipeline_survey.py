from pipeline import survey

R = lambda slug, code, rating, hours, grade=None, comment="", difficulty=None: {
    "professor_slug": slug, "course_code": code, "rating": rating, "hours": hours,
    "grade": grade, "comment": comment, "difficulty": difficulty}

def test_rows_for_professor_course_and_pair():
    rows = survey.survey_rows([R("jane-doe", "CS2500", 5, "3-4", "A", "great"),
                               R("jane-doe", "CS2500", 3, "10+", None, ""),
                               R("jane-doe", None, 4, "0-2")])
    by = {(r["professor_slug"], r["course_code"]): r for r in rows}
    assert set(by) == {("jane-doe", ""), ("", "CS2500"), ("jane-doe", "CS2500")}
    p = by[("jane-doe", "")]
    assert (p["source"], p["rating"], p["num_ratings"], p["num_comments"]) == ("survey", 4.0, 3, 1)
    assert p["hours_per_week"] == round((3.5 + 12.0 + 1.0) / 3, 2)
    assert p["rating_distribution"] == {"1": 0, "2": 0, "3": 1, "4": 1, "5": 1}
    assert by[("jane-doe", "CS2500")]["grade_distribution"] == {"A": 1}
    assert p["difficulty"] is None and p["would_take_again_pct"] is None

def test_difficulty_is_the_mean_of_ratings_that_answered_it():
    # Ratings from before the question was asked have no difficulty and don't count toward it.
    rows = survey.survey_rows([R("jane-doe", "CS2500", 5, "3-4", difficulty=4),
                               R("jane-doe", "CS2500", 3, "10+", difficulty=1),
                               R("jane-doe", "CS2500", 4, "0-2")])
    by = {(r["professor_slug"], r["course_code"]): r for r in rows}
    assert by[("jane-doe", "CS2500")]["difficulty"] == 2.5
    assert by[("jane-doe", "")]["difficulty"] == 2.5

def test_no_ratings_no_rows():
    assert survey.survey_rows([]) == []

def test_approved_course_already_in_catalog_is_not_added_twice():
    cat = [{"code": "CS4973", "name": "Special Topics", "department": "CS", "subject": "CS",
            "search_text": "cs4973", "is_placeholder": False}]
    assert survey.approved_courses([{"code": "CS4973", "name": "Topics"}], cat) == []
    added = survey.approved_courses([{"code": "CS4974", "name": "Robots"}], cat)
    assert [(c["code"], c["subject"], c["is_placeholder"]) for c in added] == [("CS4974", "CS", False)]
    assert added[0]["search_text"] == "cs4974 robots" and added[0]["department"] is None


def test_two_approvals_of_the_same_code_add_one_course():
    added = survey.approved_courses([{"code": "CS4974", "name": "A"}, {"code": "CS4974", "name": "B"}], [])
    assert [c["code"] for c in added] == ["CS4974"]
