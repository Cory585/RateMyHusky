"""sources.rmp: only what Rate My Professors measured."""

import rmp

PROF = {"rmp_rating": 4.25, "num_ratings": 12, "difficulty": 3.0,
        "would_take_again_pct": 80.0, "professor_url": "https://www.ratemyprofessors.com/professor/9",
        "avg_rating": 4.9}


def _review(quality=5, grade="A"):
    return {"course": "CS3500", "quality": quality, "difficulty": 3, "date": "2024",
            "tags": "", "attendance": "", "grade": grade, "textbook": "",
            "online_class": "", "comment": "ok"}


def test_section_keys_are_fixed():
    assert set(rmp.build_section(PROF, [])) == {
        "available", "rating", "difficulty", "wouldTakeAgainPct", "numRatings",
        "professorUrl", "ratingDistribution", "gradeDistribution", "reviews"}


def test_section_reads_the_rmp_columns_not_the_blend():
    s = rmp.build_section(PROF, [])
    assert (s["available"], s["rating"], s["difficulty"], s["wouldTakeAgainPct"], s["numRatings"]) == \
        (True, 4.25, 3.0, 80.0, 12)
    assert s["professorUrl"] == PROF["professor_url"]


def test_no_ratings_is_unavailable_with_null_numbers():
    s = rmp.build_section({**PROF, "rmp_rating": None, "num_ratings": 0}, [])
    assert s["available"] is False
    assert (s["rating"], s["difficulty"], s["wouldTakeAgainPct"]) == (None, None, None)
    assert s["professorUrl"] == PROF["professor_url"]


def test_stat_treats_zero_and_none_as_missing():
    assert rmp.stat(0, 2) is None
    assert rmp.stat(None, 2) is None
    assert rmp.stat(3.456, 2) == 3.46


def test_rating_distribution_counts_each_star():
    d = rmp.rating_distribution([_review(5), _review(5), _review(1), _review(0)])
    assert d == {"1": 1, "2": 0, "3": 0, "4": 0, "5": 2}


def test_grade_distribution_skips_non_grades():
    d = rmp.grade_distribution([_review(grade="A"), _review(grade="N/A"),
                                _review(grade="Not sure yet"), _review(grade="")])
    assert d == {"A": 1}


def test_fetch_reviews_reads_rmp_reviews_with_the_moderation_filter(monkeypatch):
    seen = []
    monkeypatch.setattr(rmp.moderation, "sql_filter", lambda alias="": " AND ok")
    rows = rmp.fetch_reviews("olin guha", lambda sql, p: seen.append(sql) or [
        {**_review(), "comment": "a &amp; b"}], lambda t: t.replace("&amp;", "&"))
    assert "FROM rmp_reviews" in seen[0] and seen[0].rstrip().endswith("AND ok")
    assert rows[0]["comment"] == "a & b"
