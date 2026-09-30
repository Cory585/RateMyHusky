"""The RMP module: RMP numbers, and only RMP numbers, for the professor page.

rmp.build_section is what the v2 payload's `sources.rmp` is. The property that
matters is that every number comes from RMP's own summary row, so several tests
hand it a catalog row carrying other values and check none of them come out.

No database: link rows and reviews are synthetic, as fetch_link_rows and
fetch_reviews would return them.
"""

from datetime import datetime, timezone

import rmp


def _link(**over):
    row = {"rmp_legacy_id": 12345, "match_method": "exact", "needs_review": False,
           "link_url": "https://www.ratemyprofessors.com/professor/12345",
           "rating": 4.2, "difficulty": 3.1, "would_take_again_pct": 88.0,
           "num_ratings": 57,
           "professor_url": "https://www.ratemyprofessors.com/professor/12345",
           "scraped_at": datetime(2026, 9, 27, 10, 5, tzinfo=timezone.utc)}
    row.update(over)
    return row


def _review(quality=5, grade="A", tags="Amazing lectures--Caring", difficulty=3):
    return {"course": "CS3500", "quality": quality, "difficulty": difficulty,
            "date": "2025-01-01", "tags": tags, "attendance": "", "grade": grade,
            "textbook": "", "online_class": "", "comment": "ok"}


# A catalog row as professors_catalog holds it: RMP columns plus the derived
# columns rmp.py must never read.
PROF = {"slug": "olin-guha", "name": "Olin Guha", "name_key": "olin guha",
        "rmp_rating": 4.1, "num_ratings": 31, "difficulty": 3.5,
        "would_take_again_pct": 80.0,
        "professor_url": "https://www.ratemyprofessors.com/professor/999",
        "avg_rating": 4.4, "total_reviews": 400}


# ── the summary is RMP's ────────────────────────────────────────────────────

def test_section_serves_the_rmp_summary_row():
    s = rmp.build_section(PROF, [_link()], [_review()])
    assert s["available"] is True and s["reason"] is None
    assert s["rating"] == 4.2
    assert s["difficulty"] == 3.1
    assert s["wouldTakeAgainPct"] == 88.0
    assert s["numRatings"] == 57
    assert s["professorUrl"].endswith("/12345")


def test_no_value_comes_from_the_derived_catalog_columns():
    """avg_rating 4.4 and total_reviews 400 are on the row;
    none may appear — the summary row is the only source of numbers."""
    s = rmp.build_section(PROF, [_link()], [])
    served = {s["rating"], s["difficulty"], s["wouldTakeAgainPct"], s["numRatings"]}
    assert not served & {4.4, 400}


def test_scrape_date_is_served_as_iso():
    s = rmp.build_section(PROF, [_link()], [])
    assert s["scrapedAt"] == "2026-09-27T10:05:00+00:00"


# ── a professor RMP has nothing for ─────────────────────────────────────────

def test_no_link_rows_means_unavailable_not_borrowed():
    """An empty result is "no RMP record": the section says so and carries no
    number, even though the catalog row has an avg_rating to hand."""
    s = rmp.build_section(PROF, [], [])
    assert s["available"] is False
    assert s["reason"] == "no_rmp_record"
    assert s["rating"] is None and s["difficulty"] is None
    assert s["numRatings"] == 0
    assert s["professorUrl"] is None


def test_rmp_page_with_no_ratings_keeps_the_link_but_no_number():
    s = rmp.build_section(PROF, [_link(rating=None, num_ratings=0)], [])
    assert s["available"] is False
    assert s["reason"] == "no_ratings"
    assert s["rating"] is None
    assert s["professorUrl"].endswith("/12345")


def test_every_section_has_the_same_keys():
    """One shape for the frontend to handle, whatever the professor has."""
    shapes = {frozenset(rmp.build_section(PROF, rows, []).keys())
              for rows in ([_link()], [], [_link(rating=None, num_ratings=0)], None)}
    assert len(shapes) == 1


# ── before precompute has built the tables ──────────────────────────────────

def test_missing_tables_fall_back_to_the_catalogs_rmp_columns():
    """None (tables absent) reads rmp_rating/num_ratings/difficulty — RMP-only
    columns — and never avg_rating, which is the blend."""
    s = rmp.build_section(PROF, None, [])
    assert s["available"] is True
    assert s["rating"] == 4.1
    assert s["numRatings"] == 31
    assert s["difficulty"] == 3.5
    assert s["scrapedAt"] is None and s["matchMethod"] is None


def test_fallback_with_no_rmp_columns_is_unavailable():
    s = rmp.build_section({**PROF, "rmp_rating": None, "num_ratings": 0,
                           "professor_url": None}, None, [])
    assert s["available"] is False and s["reason"] == "no_rmp_record"


# ── sparse data ─────────────────────────────────────────────────────────────

def test_few_ratings_are_flagged():
    assert rmp.build_section(PROF, [_link(num_ratings=2)], [])["fewRatings"] is True
    assert rmp.build_section(PROF, [_link(num_ratings=rmp.FEW_RATINGS)],
                             [])["fewRatings"] is False


# ── derived from the reviews ────────────────────────────────────────────────

def test_rating_distribution_counts_each_star():
    reviews = [_review(5), _review(5), _review(3), _review(1), _review(0)]
    d = rmp.build_section(PROF, [_link()], reviews)["ratingDistribution"]
    assert d == {"1": 1, "2": 0, "3": 1, "4": 0, "5": 2}  # quality 0 is unset


def test_grade_distribution_skips_non_grades():
    reviews = [_review(grade="A"), _review(grade="A"), _review(grade="B+"),
               _review(grade="N/A"), _review(grade="Not sure yet"),
               _review(grade="Rather not say"), _review(grade="")]
    assert rmp.build_section(PROF, [_link()], reviews)["gradeDistribution"] == {"A": 2, "B+": 1}


def test_top_tags_rank_by_count_then_name():
    reviews = [_review(tags="Caring--Tough grader"), _review(tags="Caring"),
               _review(tags="  Amazing lectures -- Caring "), _review(tags="")]
    tags = rmp.build_section(PROF, [_link()], reviews)["topTags"]
    assert tags[0] == {"tag": "Caring", "count": 3}
    assert [t["tag"] for t in tags[1:]] == ["Amazing lectures", "Tough grader"]


def test_top_tags_are_capped():
    reviews = [_review(tags="--".join(f"tag{i}" for i in range(20)))]
    assert len(rmp.top_tags(reviews)) == rmp.TOP_TAGS


# ── how the professor was matched ───────────────────────────────────────────

def test_match_method_reports_the_weakest_link():
    """Two RMP pages merged onto one professor: the section reports the less
    certain match, so an alias never hides behind an exact one."""
    rows = [_link(match_method="exact"), _link(match_method="alias", rmp_legacy_id=2)]
    s = rmp.build_section(PROF, rows, [])
    assert s["matchMethod"] == "alias"
    assert s["rmpPages"] == 2


# ── the SQL touches RMP tables only ─────────────────────────────────────────

def test_fetch_link_rows_reads_only_rmp_tables():
    seen = []
    rmp.fetch_link_rows("olin-guha", lambda sql, params: seen.append((sql, params)) or [])
    sql, params = seen[0]
    assert params == ("olin-guha",)
    assert "prof_rmp_link" in sql and "prof_rmp_summary" in sql
    for other in ("trace_", "professors_catalog", "reddit"):
        assert other not in sql


def test_fetch_reviews_reads_rmp_reviews_with_the_moderation_filter():
    import moderation
    seen = []
    rmp.fetch_reviews("olin guha", lambda sql, params: seen.append(sql) or [], lambda t: t)
    assert "FROM rmp_reviews" in seen[0]
    assert moderation.sql_filter() in seen[0]
