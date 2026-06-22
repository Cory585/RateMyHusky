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
