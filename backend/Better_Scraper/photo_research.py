"""Pure helpers for the agent-driven professor photo re-scrape.

Reuses normalization, alias, and college constants from precompute.py so the
photo pipeline groups and matches professors exactly like the app does.
"""
import os
import sys
import re

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from precompute import normalize_name, upgrade_image_url, COLLEGE_MAP, ALIAS_MAP  # noqa: E402


def build_alias_index():
    """Build {normalized_name: equivalence_set} from ALIAS_MAP.

    ALIAS_MAP keys/values are mixed case; normalize both. Each direct pair
    (src, tgt) joins their equivalence sets so any member resolves to all.
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for src, tgt in ALIAS_MAP.items():
        union(normalize_name(src), normalize_name(tgt))

    groups = {}
    for node in list(parent):
        groups.setdefault(find(node), set()).add(node)

    idx = {}
    for members in groups.values():
        for m in members:
            idx[m] = set(members)
    return idx


def aliases_for(name, idx):
    """Return the set of normalized names equivalent to `name`."""
    key = normalize_name(name)
    return set(idx.get(key, {key}))


def _parts(name):
    s = normalize_name(name)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return [p for p in s.split() if p]


def name_matches(neu_name, page_name, idx):
    """True if page_name refers to the same person as neu_name.

    Accept: exact normalized match; first+last match ignoring middle names;
    alias equivalence (either direction). Reject: surname-only, or different
    first name with no alias evidence.
    """
    neu_eq = aliases_for(neu_name, idx)
    page_norm = normalize_name(page_name)

    # Alias / exact equivalence (covers documented variants)
    if page_norm in neu_eq:
        return True
    page_eq = aliases_for(page_name, idx)
    if neu_eq & page_eq:
        return True

    np_, pp = _parts(neu_name), _parts(page_name)
    if len(np_) < 2 or len(pp) < 2:
        return False  # need at least first+last on both sides

    # first + last, ignoring middles
    return np_[0] == pp[0] and np_[-1] == pp[-1]
