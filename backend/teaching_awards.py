"""Faculty teaching awards shown on the professor page.

A curated list, committed like denylist.txt: Northeastern names a few teaching
award winners a year, on pages it publishes (the provost's University Excellence
in Teaching Award, the colleges' own awards). build_teaching_awards.py scrapes
the structured pages, merges in hand-added rows, and records on each row the
professor name_key it joins on, so serving is a dict lookup and the professor
payload keeps its four statements.

A row whose nameKey is null names someone outside the catalog. It stays in the
file so a later build can link it once that professor has RMP ratings.
"""

import json
import os
from collections import defaultdict

from denylist import is_denied_key

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teaching_awards.json")


def index_awards(rows, is_denied=is_denied_key):
    """{name_key: [award, ...]}, newest first. Unlinked and denied rows are dropped."""
    by_key = defaultdict(list)
    for r in rows:
        key = r.get("nameKey")
        if not key or is_denied(key):
            continue
        by_key[key].append({
            "award": r["award"],
            "awardingBody": r["awardingBody"],
            "year": r["year"],
            "yearLabel": r["yearLabel"],
            "sourceUrl": r["sourceUrl"],
        })
    for awards in by_key.values():
        awards.sort(key=lambda a: (-a["year"], a["award"]))
    return dict(by_key)


def load(path=PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_INDEX = None


def awards_for(name_key):
    global _INDEX
    if _INDEX is None:
        _INDEX = index_awards(load())
    return [dict(a) for a in _INDEX.get(name_key, [])]
