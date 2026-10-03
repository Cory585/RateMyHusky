"""Teaching awards: the page parsers, name linking, merge, and the served index."""

import build_teaching_awards as build
from teaching_awards import index_awards

# Trimmed from the live pages (2026-10), structure kept as served.
PROVOST = """
<h2>2026 Honorees</h2> <div class="row"> <article class="card --profile ">
<div class="__body py--1"> <h2 class="__title">Erin Islo</h2> <div class="__subtitle">Assistant Professor, Law</div> </div>
</article> <article class="card --profile "> <div class="__body py--1"> <h2 class="__title">Laurent Lessard</h2>
<div class="__subtitle">Associate Professor, Mechanical and Industrial Engineering</div> </div> </article>
<div class="modal"><h2 class="__title">Erin Islo</h2> <div class="__subtitle">Assistant Professor, Law</div></div>
<button class="__title"> Past Honorees </button> <div class="__content"> <div class="__copy">
<h3>2024-25</h3><p><strong>Daniel Aldrich</strong><br />Professor, Political Science and Public Policy</p>
<h3>2022&#8211;23</h3><p><strong>Miso Kim</strong><br />Assistant Professor of Experience Design</p>
<p><strong>Laura Kuhl</strong><br />Assistant Professor of Public Policy</p> </div> </div>
"""

KHOURY = """
<button class="accordion-header" type="button">
    Faculty recognition: Teaching	</button>
<div class="accordion-content" inert><div class="accordion-content__inner">
<p class="wp-block-paragraph"><strong>The Ruth and Joel Spira Outstanding Teacher Award</strong></p>
<ul class="wp-block-list">
<li>Daniel Patterson (2025)</li>
<li>David Bau (2024)</li>
</ul>
<p class="wp-block-paragraph"><strong>Best Teacher/Teacher of the Year Award</strong></p>
<ul class="wp-block-list">
<li>Rajmohan Rajamaran (2018)</li>
<li>Ben Lerner (2017)</li>
</ul>
</div>
</div>
<button class="accordion-header" type="button">Faculty recognition: Research</button>
<div><p><strong>Outstanding Junior Researcher</strong></p><ul><li>David Bau (2025)</li></ul></div>
"""


def test_provost_reads_current_and_past_honorees_once_each():
    rows = build.parse_provost(PROVOST)
    assert [(r["name"], r["year"], r["yearLabel"]) for r in rows] == [
        ("Erin Islo", 2026, "2025–26"),
        ("Laurent Lessard", 2026, "2025–26"),
        ("Daniel Aldrich", 2025, "2024–25"),
        ("Miso Kim", 2023, "2022–23"),
        ("Laura Kuhl", 2023, "2022–23"),
    ]
    assert rows[0]["title"] == "Assistant Professor, Law"
    assert {r["award"] for r in rows} == {"University Excellence in Teaching Award"}
    assert {r["nameKey"] for r in rows} == {None}


def test_khoury_reads_only_faculty_teaching_and_fixes_the_typo():
    rows = build.parse_khoury(KHOURY)
    assert [(r["name"], r["award"], r["year"]) for r in rows] == [
        ("Daniel Patterson", "Khoury Spira Outstanding Teacher Award", 2025),
        ("David Bau", "Khoury Spira Outstanding Teacher Award", 2024),
        ("Rajmohan Rajaraman", "Khoury Teacher of the Year", 2018),
        ("Ben Lerner", "Khoury Teacher of the Year", 2017),
    ]


def test_layout_change_parses_nothing():
    assert build.parse_provost("<html></html>") == []
    assert build.parse_khoury("<html></html>") == []


DEPTS = {"benjamin lerner": "Computer Science", "missy mcelligott": "Biology",
         "amy briesch": "Psychology", "elena strange": "Computer Science",
         "neal lerner": "English"}


def test_resolve_literal_alias_nickname_and_middle_initial():
    assert build.resolve("Benjamin Lerner", DEPTS) == ("benjamin lerner", "Computer Science")
    assert build.resolve("Ben Lerner", DEPTS) == ("benjamin lerner", "Computer Science")
    assert build.resolve("Melissa McElligott", DEPTS) == ("missy mcelligott", "Biology")
    assert build.resolve("Amy M. Briesch", DEPTS) == ("amy briesch", "Psychology")
    assert build.resolve("Laney Strange", DEPTS) == ("elena strange", "Computer Science")   # prof_aliases


def test_resolve_skips_names_checked_as_different_people():
    assert build.resolve("Patrick Jones", {"patrick jones": "Linguistics"}) == (None, None)


def test_resolve_manual_links_and_curly_apostrophes():
    depts = {"yunrong chai": "Microbiology", "brian o'connell": "Engineering"}
    assert build.resolve("Yurong Chai", depts) == ("yunrong chai", "Microbiology")
    assert build.resolve("Brian O\u2019Connell", depts) == ("brian o'connell", "Engineering")


def test_resolve_never_links_on_surname_alone():
    assert build.resolve("Joan Lerner", DEPTS) == (None, None)
    assert build.resolve("Lerner", DEPTS) == (None, None)


def _row(name, year=2020, key=None, award="Teacher of the Year"):
    return {"name": name, "title": None, "award": award, "awardingBody": "Khoury College",
            "year": year, "yearLabel": str(year), "sourceUrl": "https://x", "nameKey": key}


def test_merge_keeps_existing_rows_and_their_links():
    existing = [_row("Ben Lerner", 2017, key="hand set key")]
    scraped = [_row("Ben  Lerner", 2017), _row("Chieh Wu", 2025)]
    merged = build.merge(existing, scraped)
    assert [(r["name"], r["nameKey"]) for r in merged] == [("Ben Lerner", "hand set key"), ("Chieh Wu", None)]


def test_link_fills_unlinked_rows_and_drops_denied():
    rows = [_row("Ben Lerner"), _row("Neal Lerner", key="neal lerner"), _row("Nobody Here")]
    build.link(rows, DEPTS, is_denied=lambda k: k == "neal lerner")
    assert [(r["name"], r["nameKey"]) for r in rows] == [("Ben Lerner", "benjamin lerner"), ("Nobody Here", None)]


def test_index_groups_newest_first_and_skips_unlinked_and_denied():
    rows = [_row("Ben Lerner", 2017, key="benjamin lerner"),
            _row("Benjamin Lerner", 2017, key="benjamin lerner", award="Excellence in Teaching Award"),
            _row("Ben Lerner", 2021, key="benjamin lerner"),
            _row("Nobody Here"),
            _row("Neal Lerner", key="neal lerner")]
    idx = index_awards(rows, is_denied=lambda k: k == "neal lerner")
    assert set(idx) == {"benjamin lerner"}
    assert [(a["year"], a["award"]) for a in idx["benjamin lerner"]] == [
        (2021, "Teacher of the Year"), (2017, "Excellence in Teaching Award"), (2017, "Teacher of the Year")]
    assert set(idx["benjamin lerner"][0]) == {"award", "awardingBody", "year", "yearLabel", "sourceUrl"}


def test_committed_file_is_well_formed():
    rows = build.load()
    assert len({build.row_key(r) for r in rows}) == len(rows)
    for r in rows:
        assert set(r) >= {"name", "award", "awardingBody", "year", "yearLabel", "sourceUrl", "nameKey"}
        assert isinstance(r["year"], int) and r["sourceUrl"].startswith("https://")
