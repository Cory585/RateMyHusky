import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

import photo_research as pr


def test_alias_index_links_both_directions():
    idx = pr.build_alias_index()
    # ALIAS_MAP has "bill goldman" / "william (bill) goldman" -> "william goldman"
    eq = pr.aliases_for("Bill Goldman", idx)
    assert "william goldman" in eq
    assert "bill goldman" in eq


def test_aliases_for_unknown_name_returns_self_only():
    idx = pr.build_alias_index()
    eq = pr.aliases_for("Zzz Nobodyson", idx)
    assert eq == {"zzz nobodyson"}


def test_name_match_exact_and_first_last():
    idx = pr.build_alias_index()
    assert pr.name_matches("Jonathan Bell", "Jonathan Bell", idx)
    # middle name on page, not in NEU name
    assert pr.name_matches("Jonathan Bell", "Jonathan P. Bell", idx)
    # middle name in NEU name, not on page
    assert pr.name_matches("Jonathan P Bell", "Jonathan Bell", idx)


def test_name_match_rejects_surname_only_and_wrong_first():
    idx = pr.build_alias_index()
    # same surname, different first name, no alias -> reject (collision)
    assert not pr.name_matches("Jonathan Bell", "Sarah Bell", idx)
    # only surname present -> reject
    assert not pr.name_matches("Jonathan Bell", "Bell", idx)


def test_name_match_accepts_alias():
    idx = pr.build_alias_index()
    # alias pair from ALIAS_MAP
    assert pr.name_matches("Virgil Pavlu", "Virgiliu Pavlu", idx)
