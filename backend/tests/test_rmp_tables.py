"""prof_identity / prof_rmp_link / prof_rmp_summary, as precompute builds them.

The split exists so no RMP value is computed from, or averaged with, another
source, and so every professor-to-RMP link records how it was made. These pin
both, driven by synthetic frames — no database.
"""

import pathlib
import re
from datetime import datetime, timezone

import pandas as pd

import prof_aliases
from precompute import (CATALOG_COLUMNS, assign_rmp_keys, identity_rows,
                        rmp_difficulty, rmp_legacy_id, rmp_link_report,
                        rmp_link_rows, rmp_scraped_at, rmp_summary_rows, rmp_wta)
from prof_aliases import RMP_MATCH_METHODS, rmp_link_key

PRECOMPUTE_PY = pathlib.Path(__file__).resolve().parent.parent / "precompute.py"


def listings(rows):
    """Raw rmp_professors rows, one per RMP page, before merge_rmp_aliases."""
    return pd.DataFrame([{"name": n, "department": d,
                          "professor_url": f"https://www.ratemyprofessors.com/professor/{i}"}
                         for n, d, i in rows])


# ── how a listing is matched ────────────────────────────────────────────────

def test_plain_name_is_an_exact_match():
    assert rmp_link_key("Olin  Guha") == ("olin guha", "exact")


def test_alias_map_entry_is_an_alias_match():
    assert rmp_link_key("Chris Bosso") == ("christopher bosso", "alias")


def test_manual_link_wins_and_is_recorded_as_manual(monkeypatch):
    monkeypatch.setitem(prof_aliases.RMP_MANUAL_LINKS, "chris bosso", "someone else")
    assert rmp_link_key("Chris Bosso") == ("someone else", "manual")


def test_listings_and_reviews_resolve_through_the_same_key(monkeypatch):
    """A manual link must move a listing's ratings with it. precompute keys
    rmp_reviews by rmp_link_key(professor_name) and listings by assign_rmp_keys;
    if those disagreed, the recount would publish 0 ratings under the new key."""
    monkeypatch.setitem(prof_aliases.RMP_MANUAL_LINKS, "j smith", "jane smith")
    df = assign_rmp_keys(listings([("J Smith", "Biology", 1)]))
    assert df.at[0, "_name_key"] == rmp_link_key("J Smith")[0] == "jane smith"
    assert df.at[0, "_match_method"] == "manual"


def test_legacy_id_parsed_from_the_profile_url():
    assert rmp_legacy_id("https://www.ratemyprofessors.com/professor/2875022") == 2875022
    assert rmp_legacy_id("") is None and rmp_legacy_id(None) is None


def test_every_method_the_code_can_emit_is_allowed_by_the_ddl():
    """The CHECK constraint and RMP_MATCH_METHODS must list the same values, or
    the insert fails mid-rebuild against the real database."""
    src = PRECOMPUTE_PY.read_text(encoding="utf-8")
    check = re.search(r"CHECK \(match_method IN \((.*?)\)\)", src, re.S).group(1)
    assert {v.strip(" '\n") for v in check.split(",")} == set(RMP_MATCH_METHODS)


# ── prof_rmp_link ───────────────────────────────────────────────────────────

def test_one_link_row_per_rmp_page_even_after_the_merge():
    raw = assign_rmp_keys(listings([("Chris Bosso", "Political Science", 10),
                                    ("Christopher Bosso", "Political Science", 11)]))
    rows = rmp_link_rows(raw, {"christopher bosso": "christopher-bosso"})
    assert [(r[0], r[1], r[5]) for r in rows] == [
        ("christopher-bosso", 10, "alias"), ("christopher-bosso", 11, "exact")]


def test_merged_pages_are_flagged_for_review():
    """Two pages folded onto one professor get their ratings averaged together,
    and nothing lexical says whether that is one person or two."""
    raw = assign_rmp_keys(listings([("Chris Bosso", "Poli Sci", 10),
                                    ("Christopher Bosso", "Poli Sci", 11)]))
    rows = rmp_link_rows(raw, {"christopher bosso": "christopher-bosso"})
    assert all(r[6] for r in rows)
    assert all("2 RMP pages merged" in r[7] for r in rows)


def test_a_single_exact_page_is_not_flagged():
    raw = assign_rmp_keys(listings([("Olin Guha", "Computer Science", 5)]))
    (row,) = rmp_link_rows(raw, {"olin guha": "olin-guha"})
    assert row[6] is False and row[7] is None


def test_pages_with_no_catalog_row_are_dropped():
    """Denylisted or cleaned-out professors must not leave a dangling link."""
    raw = assign_rmp_keys(listings([("Olin Guha", "CS", 5), ("Denied Person", "CS", 6)]))
    rows = rmp_link_rows(raw, {"olin guha": "olin-guha"})
    assert [r[0] for r in rows] == ["olin-guha"]


# ── prof_rmp_summary ────────────────────────────────────────────────────────

SCRAPED = datetime(2026, 9, 27, tzinfo=timezone.utc)


def merged(**over):
    """An rmp_profs row after merge + recount, with the TRACE columns the
    catalog build attaches — which the summary must ignore."""
    row = {"_name_key": "olin guha", "rating": 4.2, "num_ratings": 57,
           "level_of_difficulty": "3.1", "would_take_again_pct": "88%",
           "professor_url": "https://www.ratemyprofessors.com/professor/5",
           "trace_overall": 4.9, "trace_reviews": 300, "avg_rating": 4.55}
    row.update(over)
    return pd.DataFrame([row])


def test_summary_carries_rmp_values_only():
    (row,) = rmp_summary_rows(merged(), {"olin guha": "olin-guha"}, {"olin guha": 40}, SCRAPED)
    slug, rating, diff, wta, n, comments, url, scraped = row
    assert (slug, rating, diff, wta, n, comments) == ("olin-guha", 4.2, 3.1, 88.0, 57, 40)
    assert url.endswith("/5") and scraped == SCRAPED
    # 4.9 (TRACE) and 4.55 (the blend) are on the row; neither is in the summary.
    assert not {4.9, 4.55, 300} & set(row[1:6])


def test_summary_with_no_ratings_has_no_rating():
    (row,) = rmp_summary_rows(merged(num_ratings=0), {"olin guha": "olin-guha"}, {}, None)
    assert row[1] is None and row[4] == 0


def test_rmp_field_parsers():
    assert rmp_wta("83%") == 83.0 and rmp_wta("N/A") is None and rmp_wta("-1") is None
    assert rmp_wta(float("nan")) is None
    assert rmp_difficulty("3.14159") == 3.14 and rmp_difficulty("0") is None
    assert rmp_difficulty(None) is None


# ── prof_identity ───────────────────────────────────────────────────────────

def test_identity_is_projected_from_the_catalog_with_no_ratings():
    row = [None] * len(CATALOG_COLUMNS)
    for col, v in {"slug": "olin-guha", "name": "Olin Guha", "name_key": "olin guha",
                   "department": "CS", "college": "Khoury", "image_url": "x.jpg",
                   "focus_x": 50.0, "focus_y": 30.0, "avg_rating": 4.4,
                   "rmp_rating": 4.2}.items():
        row[CATALOG_COLUMNS.index(col)] = v
    assert identity_rows([tuple(row)]) == [
        ("olin-guha", "Olin Guha", "olin guha", "CS", "Khoury", "x.jpg", 50.0, 30.0)]


# ── scrape date ─────────────────────────────────────────────────────────────

def test_scraped_at_override(monkeypatch):
    monkeypatch.setenv("RMP_SCRAPED_AT", "2026-09-20T10:00:00Z")
    assert rmp_scraped_at("/nonexistent.csv") == datetime(2026, 9, 20, 10, tzinfo=timezone.utc)


def test_scraped_at_from_csv_mtime(monkeypatch, tmp_path):
    monkeypatch.delenv("RMP_SCRAPED_AT", raising=False)
    f = tmp_path / "rmp_professors.csv"
    f.write_text("name\n")
    got = rmp_scraped_at(str(f))
    assert got.tzinfo is not None
    assert abs(got.timestamp() - f.stat().st_mtime) < 1


def test_scraped_at_unknown_is_none(monkeypatch):
    monkeypatch.delenv("RMP_SCRAPED_AT", raising=False)
    assert rmp_scraped_at("/nonexistent/rmp_professors.csv") is None


# ── the manual-review report ────────────────────────────────────────────────

def test_report_lists_flagged_rmp_links_and_every_trace_fuzzy_match():
    links = [("a", 1, "A", "CS", "u", "exact", False, None),
             ("b", 2, "B", "CS", "u", "alias", True, "2 RMP pages merged into one professor")]
    fuzzy = [("dan-koloski", "dan koloski", "daniel koloski")]
    report = rmp_link_report(links, fuzzy)
    assert [(r[0], r[1]) for r in report] == [("rmp", "b"), ("trace", "dan-koloski")]
    assert report[1][3] == "daniel koloski" and report[1][6] == "fuzzy"
