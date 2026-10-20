"""Give Banner instructors the matcher couldn't place their own professor page.

An instructor gets a page only when every row for its instructor_key is
no_match: any other verdict (ambiguous, subject_mismatch, alias_unverified,
roster_duplicate) means the matcher found a candidate or flagged a conflict,
and a new page could duplicate a real professor. A key with a no_match row and
another verdict is counted as mixed; one with no no_match row is already matched.
A key whose first and last words equal another known key's (an added or missing
middle name, or a Jr/Sr/II/III/IV suffix) is a possible duplicate, whichever side
has the extra word. A new professor's name_key is
Banner's instructor_key verbatim. Existing slugs and names are never changed.
"""

import os
import re
import sys
from collections import Counter, defaultdict

from denylist import is_denied, is_denied_key
from teaching_history import display_name

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scraper"))
from banner_api import NON_TEACHING_SCHEDULE_TYPES  # noqa: E402
from banner_history import MAX_COURSES_PER_TERM  # noqa: E402
from match_banner_instructors import BANNER_ALIASES, subject_of  # noqa: E402

from .db import chunk_insert
from .names import get_college, name_to_slug
from .roster import unique_slug
from .seed import PROFESSOR_FIELDS

BANNER_FIELDS = PROFESSOR_FIELDS + ("origin",)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

# Law and CPS run their own term calendars, so the track of the terms someone
# teaches in names their college where an RMP department label cannot: CPS
# faculty are labelled by subject (Management, Regulatory Affairs) and land in
# other colleges.
# CPS Semester is left out on purpose. Its sections are CPS undergraduate
# courses (CHM, BIO, ENG are the catalog's "Chemistry - CPS" and so on, not the
# day college's CHEM, BIOL, ENGW), but day-college faculty teach many of them
# alongside their own: Christopher Toher (Chemistry, 112 ratings) has 49 CHM
# sections and 14 CHEM. Counting it would move such professors into
# Professional Studies.
#
# Both tracks are ending. Law and CPS Semester terms stop after Summer 2025, when
# Law moved onto the university Semester calendar, so a LAW-subject section
# counts as Law Semester whatever its term. CPS Quarter stops after Summer 2026
# (the registrar moves graduate CPS to Semester from Fall 2026), and CPS has no
# subject of its own to stand in, so Professional Studies is decided by the
# quarters taught up to then.
TRACK_COLLEGES = {"Law Semester": "Law", "CPS Quarter": "Professional Studies"}

# Subjects the catalog names "<subject> - CPS" or "- CPS Specialty"
# (catalog.northeastern.edu/course-descriptions/, read 2026-10-19). A CPS
# subject belongs to Professional Studies whoever votes on it.
CPS_SUBJECTS = frozenset("""
    AAI ACC ALY ANT APL ART AVM BIO BTC CED CET CHM CMG CMMN CMN COP DGM ECN
    ECNM EDU EET ENG ESC ESLG FIN GET GIS GST HMG HRM HSC HST HSTY HSV INS INT
    ITC LDR LST LWP MATM MET MGT MKT MTH MUS NPM NTR PBR PDM PHL PHLS PHY PJM
    PLSC POL PST PSY RGA RMS SCLY SIA SLP SOC TCC
""".split())

# Sections taught per professor and track, counted the way
# the Banner history counts teaching: Banner's own rows, no supervision or co-op
# rows, no cancelled sections (closed term, 0 enrolled), and no term where the
# name was listed twice or sat on more than MAX_COURSES_PER_TERM courses (a
# coordinator, not a teacher). Cancelled sections still count toward that course
# limit, as they do in banner_history.roster_artifacts. The slug comes from the
# matcher's per-course verdict, so a namesake's courses the matcher refused to
# link stay uncounted. A section joins through any spelling the matcher merged
# into the row (instructor_keys), and is counted once per professor however many
# spellings listed it, as build_course_instructors dedupes sections. A linked
# row's instructor_key is NULL; an unlinked row (a Banner-only professor, linked
# at apply time) keeps its key and a NULL slug.
TRACK_SECTIONS_SQL = """
    WITH taught AS (
        SELECT i.term_code, i.crn, i.instructor_key, s.subject_course,
               CASE WHEN s.subject = 'LAW' THEN 'Law Semester' ELSE t.track END AS track,
               coalesce(t.scraped_closed, false) AND coalesce(s.enrollment = 0, false) AS cancelled
        FROM banner_section_instructors i
        JOIN banner_sections s ON s.term_code = i.term_code AND s.crn = i.crn
        JOIN banner_terms t ON t.term_code = i.term_code
        WHERE i.source = 'banner'
          AND lower(coalesce(s.schedule_type, '')) NOT IN ({non_teaching})
          AND NOT EXISTS (SELECT 1 FROM banner_roster_duplicates d
                          WHERE d.term_code = i.term_code AND d.instructor_key = i.instructor_key)
    ), coordinators AS (
        SELECT term_code, instructor_key FROM taught GROUP BY 1, 2
        HAVING count(DISTINCT subject_course) > {max_courses}
    )
    SELECT CASE WHEN b.professor_slug IS NULL THEN b.instructor_key END AS instructor_key,
           b.professor_slug, t.track, count(DISTINCT (t.term_code, t.crn)) AS sections
    FROM taught t
    -- instructor_keys is NULL until the matcher's first run that writes it.
    JOIN banner_course_instructors b
      ON b.subject_course = t.subject_course
     AND t.instructor_key = ANY(string_to_array(coalesce(b.instructor_keys, b.instructor_key), '|'))
    WHERE NOT t.cancelled
      AND NOT EXISTS (SELECT 1 FROM coordinators c
                      WHERE c.term_code = t.term_code AND c.instructor_key = t.instructor_key)
    GROUP BY 1, 2, 3
""".format(non_teaching=", ".join(f"'{s}'" for s in sorted(NON_TEACHING_SCHEDULE_TYPES)),
           max_courses=int(MAX_COURSES_PER_TERM))


def _first_last(key):
    parts = [w for w in re.sub(r"[.,]", "", key).split() if w not in _SUFFIXES]
    return f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else None


def is_denied_banner_key(key):
    """Denied as the key itself, or under either alias map's target. A denied
    professor is not in the catalog, so the matcher reports an aliased spelling
    of them as no_match rather than resolving it."""
    return is_denied_key(key) or is_denied(key) or is_denied_key(BANNER_ALIASES.get(key))


def subject_departments(instructor_rows, department_by_slug):
    """subject -> the department most often held by matched professors teaching it."""
    votes = defaultdict(Counter)
    for row in instructor_rows:
        department = department_by_slug.get(row["professor_slug"]) if row["professor_slug"] else None
        if department:
            votes[subject_of(row["subject_course"])][department] += 1
    return {subject: min(counts, key=lambda d: (-counts[d], d)) for subject, counts in votes.items()}


def _track_sections(track_rows, new_slug_by_key):
    """slug -> Counter(track -> sections). An unlinked row counts only for a
    Banner-only professor created this run (new_slug_by_key), whose rows are all
    unlinked by construction. Rows that resolve to the same slug are summed."""
    sections = defaultdict(Counter)
    for row in track_rows:
        slug = row["professor_slug"] or new_slug_by_key.get(row["instructor_key"])
        if slug:
            sections[slug][row["track"]] += row["sections"]
    return sections


def track_colleges(track_rows, new_slug_by_key):
    """slug -> college, for professors who taught more than half their sections
    in one TRACK_COLLEGES track. track_rows are TRACK_SECTIONS_SQL rows."""
    colleges = {}
    for slug, by_track in _track_sections(track_rows, new_slug_by_key).items():
        total = sum(by_track.values())
        for track, college in TRACK_COLLEGES.items():
            if by_track[track] * 2 > total:
                colleges[slug] = college
    return colleges


def subject_colleges(instructor_rows, professors):
    """subject -> college. A CPS_SUBJECTS subject is Professional Studies: its
    teachers are CPS adjuncts labelled by subject, so a vote would file MGT
    under Business. Any other subject is voted as subject_departments does, but
    without the professors labelled with a TRACK_COLLEGES college: their labels
    are the ones in question, and CRIM taught by mislabelled Law professors
    would otherwise vote itself back into Law."""
    voters = {p["slug"]: p["department"] for p in professors
              if p.get("origin") != "banner" and p.get("college") not in TRACK_COLLEGES.values()}
    colleges = {s: get_college(d) for s, d in subject_departments(instructor_rows, voters).items()}
    for row in instructor_rows:
        if subject_of(row["subject_course"]) in CPS_SUBJECTS:
            colleges[subject_of(row["subject_course"])] = "Professional Studies"
    return colleges


def track_exits(track_rows, new_slug_by_key, professors, instructor_rows, colleges_by_subject):
    """slug -> college, for professors labelled Law who taught Banner sections
    but none in a Law Semester term or LAW course: a label the term track
    contradicts outright. A professor with any Law section keeps the label (Law
    faculty who also teach a day-college course), and so does one with no Banner
    record. The new college is the one most of their sections' subjects belong
    to (colleges_by_subject), Professional Studies included; a professor whose
    subjects point to Law, Other or nowhere keeps the label rather than getting
    a guess.

    Professional Studies labels are not checked: with CPS Quarter ending, an
    absent quarter will soon say nothing about a CPS professor."""
    track_of = {"Law": "Law Semester"}
    sections = _track_sections(track_rows, new_slug_by_key)
    subjects = defaultdict(Counter)
    for row in instructor_rows:
        # Resolved as _track_sections does, so a Banner-only professor created
        # this run votes with the sections that put them in by_track.
        slug = row["professor_slug"] or new_slug_by_key.get(row["instructor_key"])
        if slug:
            subjects[slug][subject_of(row["subject_course"])] += row["sections"]
    exits = {}
    for p in professors:
        track = track_of.get(p.get("college"))
        by_track = sections.get(p["slug"])
        if not track or not by_track or by_track[track]:
            continue
        votes = Counter()
        for subject, n in subjects[p["slug"]].items():
            if colleges_by_subject.get(subject):
                votes[colleges_by_subject[subject]] += n
        if not votes:
            continue
        college = min(votes, key=lambda c: (-votes[c], c))
        if college not in ("Law", "Other"):
            exits[p["slug"]] = college
    return exits


def resolve_banner_professors(instructor_rows, professors, departments, is_denied=is_denied_banner_key):
    """(new professor rows, counts). Pure; inputs are not modified."""
    by_key = defaultdict(list)
    for row in instructor_rows:
        by_key[row["instructor_key"]].append(row)
    known = {p["name_key"] for p in professors}
    by_first_last = defaultdict(set)
    for k in known:
        if _first_last(k):
            by_first_last[_first_last(k)].add(k)
    taken = {p["slug"] for p in professors}
    new_professors = []
    counts = dict.fromkeys(("created", "known", "possible_duplicate", "mixed", "matched", "denied"), 0)
    for key in sorted(by_key):
        rows = by_key[key]
        unmatched = [r["match_method"] == "no_match" for r in rows]
        if not any(unmatched):
            counts["matched"] += 1
            continue
        if not all(unmatched):
            counts["mixed"] += 1
            continue
        if is_denied(key):
            counts["denied"] += 1
            continue
        if key in known:
            counts["known"] += 1
            continue
        first_last = _first_last(key)
        if first_last and by_first_last[first_last] - {key}:
            counts["possible_duplicate"] += 1
            continue
        terms = Counter()
        for r in rows:
            terms[subject_of(r["subject_course"])] += r["terms_taught"]
        subject = min(terms, key=lambda s: (-terms[s], s))
        department = departments.get(subject)
        newest = max(rows, key=lambda r: (r["last_term_code"] or "", r["instructor_name"] or ""))
        slug = unique_slug(name_to_slug(key), taken)
        new_professors.append({"slug": slug, "name": display_name(newest["instructor_name"]),
                               "name_key": key, "department": department,
                               "college": get_college(department),
                               "image_url": None, "focus_x": None, "focus_y": None,
                               "origin": "banner"})
        known.add(key)
        if first_last:
            by_first_last[first_last].add(key)
        taken.add(slug)
        counts["created"] += 1
    return new_professors, counts


def apply_banner_professors(conn, rows):
    cur = conn.cursor()
    chunk_insert(cur, f"INSERT INTO professors ({', '.join(BANNER_FIELDS)}) VALUES %s "
                      "ON CONFLICT (slug) DO NOTHING",
                 [tuple(r[f] for f in BANNER_FIELDS) for r in rows])
    # Link their Banner rows now, so a new page lists its courses before the next
    # banner-refresh, whose matcher recomputes these columns.
    cur.execute("UPDATE banner_course_instructors bci SET professor_slug = p.slug, "
                "name_key = p.name_key, match_method = 'name_key' FROM professors p "
                "WHERE p.slug = ANY(%s) AND p.origin = 'banner' AND p.name_key = bci.instructor_key "
                "AND bci.match_method = 'no_match'", ([r["slug"] for r in rows],))
    conn.commit()
