"""Refresh teaching_awards.json from the pages Northeastern publishes.

Two pages have a stable structure and are scraped here:

  provost  University Excellence in Teaching Award, 2010-11 to now
  khoury   Khoury's past-award-winners page (Spira Outstanding Teacher,
           Teacher of the Year)

The other colleges announce their teaching awards in one-off news posts, so
those rows are added to the JSON by hand with their sourceUrl. A build merges
the scraped rows in and never removes a row: a hand-added row is on no page
this script reads.

Each row's nameKey is the professor name_key it joins on (teaching_awards.py).
A row keeps the nameKey it has, so a hand correction survives the next build;
only rows still unlinked are resolved again, against the catalog's names:

  python build_teaching_awards.py --professors ../../RateMyHusky-data/rmp_professors.csv
  python build_teaching_awards.py --db            # professors table instead
  python build_teaching_awards.py ... --apply     # write; dry run by default

Resolution is deliberately narrow: the name as written (through prof_aliases),
then a short nickname table, then first + last with middle names dropped. A
surname alone never links. Every link is printed with the professor's
department beside the award's own title, for a reader to check before --apply.
"""

import argparse
import csv
import html
import json
import re
import sys

import requests

from denylist import is_denied_key
from pipeline.names import normalize_name
from prof_aliases import rmp_link_key
from teaching_awards import PATH, load

PROVOST_URL = "https://academic-honors.provost.northeastern.edu/faculty-awards/excellence-in-teaching-award/"
KHOURY_URL = "https://khoury.northeastern.edu/awards/past-award-winners/"

# Cloudflare in front of the provost site refuses requests' default agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"}

# Award names are display text and say who gave the award ("CSSH Outstanding
# Teaching Award"): the page shows the name alone, the body only on hover.
KHOURY_AWARDS = {
    "The Ruth and Joel Spira Outstanding Teacher Award": "Khoury Spira Outstanding Teacher Award",
    "Best Teacher/Teacher of the Year Award": "Khoury Teacher of the Year",
}

# Misspellings on the source pages, corrected before matching.
NAME_FIXES = {
    "rajmohan rajamaran": "Rajmohan Rajaraman",
}

# A winner printed differently from their RMP listing, each checked by hand
# against the department: printed name -> the name RMP lists (prof_aliases
# applies on top, as it does to every RMP listing).
MANUAL_LINKS = {
    "yurong chai": "yunrong chai",                     # COS 2018, Biology; the page drops an n
    "heidi kevoe-feldman": "heidi kevoe-feldmen",      # Communication Studies; RMP's spelling
    "risa kitagawa": "risa kitagawa amano",            # Political Science
    "bala maheswaran": "balasubrama maheswaran",       # COE teaching professor; RMP truncates
    "sarah fuchs hayat": "sara hayat",                 # Architecture
}

# Winners whose name matches a catalog professor who may be someone else, so
# the row stays unlinked. Checked by hand against the award's department.
NOT_THE_SAME_PERSON = {
    "patrick jones",   # CAMD postdoc in Communication Studies; RMP's is in Linguistics
}

NICKNAMES = {
    "ben": "benjamin", "mike": "michael", "matt": "matthew", "nate": "nathaniel",
    "dan": "daniel", "dave": "david", "jim": "james", "joe": "joseph", "tom": "thomas",
    "chris": "christopher", "nick": "nicholas", "rob": "robert", "bob": "robert",
    "bill": "william", "will": "william", "steve": "steven", "tony": "anthony",
    "missy": "melissa", "sue": "susan", "tim": "timothy",
}
# Either side may carry the nickname: "Mike Shah" won an award RMP lists as
# Michael Shah, and RMP lists Melissa McElligott as Missy.
FIRST_NAME_VARIANTS = {}
for _nick, _formal in NICKNAMES.items():
    FIRST_NAME_VARIANTS.setdefault(_nick, []).append(_formal)
    FIRST_NAME_VARIANTS.setdefault(_formal, []).append(_nick)


def _text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _row(name, title, award, body, year, label, url):
    name = _text(name)
    name = NAME_FIXES.get(normalize_name(name), name)
    return {"name": name, "title": title, "award": award, "awardingBody": body,
            "year": year, "yearLabel": label, "sourceUrl": url, "nameKey": None}


def _academic_label(end_year):
    return f"{end_year - 1}–{str(end_year)[2:]}"


def parse_provost(page):
    """Current honorees (profile cards) plus the Past Honorees accordion."""
    rows = []
    award, body = "University Excellence in Teaching Award", "Northeastern University"
    current = re.search(r"<h2>\s*(\d{4}) Honorees\s*</h2>(.*?)Past Honorees", page, re.S)
    if current:
        year = int(current.group(1))
        for name, title in re.findall(
                r'<h2 class="__title">(.*?)</h2>\s*<div class="__subtitle">(.*?)</div>', current.group(2), re.S):
            rows.append(_row(name, _text(title), award, body, year, _academic_label(year), PROVOST_URL))
    past = page.split("Past Honorees", 1)[1] if "Past Honorees" in page else ""
    for label, block in re.findall(r"<h3>(.*?)</h3>(.*?)(?=<h3>|</div>)", past, re.S):
        m = re.match(r"(\d{4})\s*[-–]\s*(\d{2})$", _text(label))
        if not m:
            continue
        year = int(m.group(1)) + 1
        for name, title in re.findall(r"<strong>(.*?)</strong>\s*<br\s*/?>(.*?)</p>", block, re.S):
            rows.append(_row(name, _text(title), award, body, year, _academic_label(year), PROVOST_URL))
    return _dedupe(rows)


def parse_khoury(page):
    """Only the faculty teaching section; student, research and staff awards are not ours."""
    m = re.search(r"Faculty recognition: Teaching\s*</button>(.*?</ul>)\s*</div>\s*</div>", page, re.S)
    if not m:
        return []
    rows = []
    for header, items in re.findall(r"<strong>(.*?)</strong>.*?<ul[^>]*>(.*?)</ul>", m.group(1), re.S):
        award = KHOURY_AWARDS.get(_text(header))
        if award is None:
            continue
        for item in re.findall(r"<li>(.*?)</li>", items, re.S):
            im = re.match(r"(.+?)\s*\((\d{4})\)$", _text(item))
            if im:
                year = int(im.group(2))
                rows.append(_row(im.group(1), None, award, "Khoury College", year, str(year), KHOURY_URL))
    return rows


def _dedupe(rows):
    seen, out = set(), []
    for r in rows:
        k = row_key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def row_key(r):
    return (normalize_name(r["name"]), r["award"], r["year"])


def name_candidates(name):
    """name_keys to try, most literal first."""
    key = rmp_link_key(name)[0]
    tokens = re.sub(r"[^a-z\s'-]", " ", normalize_name(name)).split()
    out = [key]
    if len(tokens) >= 2:
        first, last = tokens[0], tokens[-1]
        for f in [first, *FIRST_NAME_VARIANTS.get(first, [])]:
            out.append(rmp_link_key(f"{f} {last}")[0])
    return list(dict.fromkeys(out))


def resolve(name, departments):
    """(name_key, department) of the one catalog professor `name` names, else (None, None)."""
    name = name.replace("\u2019", "'")   # O’Connell
    printed = normalize_name(name)
    if printed in NOT_THE_SAME_PERSON:
        return None, None
    if printed in MANUAL_LINKS:
        key = rmp_link_key(MANUAL_LINKS[printed])[0]
        return (key, departments[key]) if key in departments else (None, None)
    for key in name_candidates(name):
        if key in departments:
            return key, departments[key]
    return None, None


def merge(existing, scraped):
    """Existing rows first, in their order, then scraped rows not yet in the file."""
    have = {row_key(r) for r in existing}
    return [dict(r) for r in existing] + [dict(r) for r in scraped if row_key(r) not in have]


def link(rows, departments, is_denied=is_denied_key):
    """Fill nameKey on unlinked rows; drop rows naming a denied professor. Returns the log lines."""
    log = []
    kept = []
    for r in rows:
        key = r.get("nameKey")
        if not key:
            key, dept = resolve(r["name"], departments)
            if key:
                r["nameKey"] = key
                log.append(f"  linked   {r['name']!r} -> {key!r} [{dept}]  ({r['title'] or r['awardingBody']})")
        if key and is_denied(key):
            continue
        kept.append(r)
    rows[:] = kept
    return log


def departments_from_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return {rmp_link_key(r["name"])[0]: r["department"] for r in csv.DictReader(f)}


def departments_from_db():
    from pipeline.db import connect
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT name_key, department FROM professors")
        return dict(cur.fetchall())


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--professors", help="CSV with name,department (the data store's rmp_professors.csv)")
    src.add_argument("--db", action="store_true", help="match against the professors table")
    ap.add_argument("--apply", action="store_true", help="write teaching_awards.json")
    args = ap.parse_args(argv)

    scraped = []
    for label, url, parse in (("provost", PROVOST_URL, parse_provost), ("khoury", KHOURY_URL, parse_khoury)):
        found = parse(fetch(url))
        if not found:
            sys.exit(f"{label}: no rows parsed from {url}; the page layout has changed")
        print(f"{label}: {len(found)} rows")
        scraped += found

    existing = load()
    rows = merge(existing, scraped)
    print(f"{len(rows) - len(existing)} new rows")
    departments = departments_from_db() if args.db else departments_from_csv(args.professors)
    for line in link(rows, departments):
        print(line)
    unlinked = [r for r in rows if not r.get("nameKey")]
    print(f"{len(rows) - len(unlinked)} linked, {len(unlinked)} not in the catalog:")
    for r in unlinked:
        print(f"  {r['name']} ({r['awardingBody']}, {r['yearLabel']})")

    if not args.apply:
        print("dry run; --apply to write")
        return
    rows.sort(key=lambda r: (r["awardingBody"], r["award"], -r["year"], r["name"]))
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {PATH}")


if __name__ == "__main__":
    main()
