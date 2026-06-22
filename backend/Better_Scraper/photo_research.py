"""Pure helpers for the agent-driven professor photo re-scrape.

Reuses normalization, alias, and college constants from precompute.py so the
photo pipeline groups and matches professors exactly like the app does.
"""
import os
import sys

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
