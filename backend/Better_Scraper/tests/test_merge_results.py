import os, sys, csv
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import merge_results as mr


def _prof(name, college="Khoury", dept="Computer Science", existing=""):
    return {"name": name, "college": college, "department": dept,
            "existing_url": existing}


def test_decide_row_new_beats_old():
    prof = _prof("Jane Doe", existing="https://x.edu/old.jpg")
    found = {"image_url": "https://x.edu/new-400x400.jpg", "source_page": "p",
             "source_tier": "neu", "confidence": "high", "matched_name": "Jane Doe"}
    row = mr.decide_row(prof, found)
    assert row["status"] == "new"
    assert row["new_url"] == "https://x.edu/new-400x400.jpg"
    assert row["old_url"] == "https://x.edu/old.jpg"


def test_decide_row_keeps_old_when_nothing_found():
    prof = _prof("Jane Doe", existing="https://x.edu/old.jpg")
    row = mr.decide_row(prof, None)
    assert row["status"] == "kept_old"
    assert row["new_url"] == "https://x.edu/old.jpg"


def test_decide_row_not_found_when_no_old_no_new():
    row = mr.decide_row(_prof("Jane Doe"), None)
    assert row["status"] == "not_found"
    assert row["new_url"] == ""


def test_decide_row_rejects_implausible_url_falls_back_to_old():
    prof = _prof("Jane Doe", existing="https://x.edu/old.jpg")
    found = {"image_url": "https://x.edu/logo.png", "source_page": "p",
             "source_tier": "web", "confidence": "low", "matched_name": "Jane Doe"}
    row = mr.decide_row(prof, found)
    assert row["status"] == "kept_old"


def test_apply_dedup_drops_shared_url():
    rows = [
        {"name": "A One", "new_url": "https://x.edu/shared.jpg", "status": "new"},
        {"name": "B Two", "new_url": "https://x.edu/shared.jpg", "status": "new"},
        {"name": "C Three", "new_url": "https://x.edu/shared.jpg", "status": "new"},
    ]
    out = mr.apply_dedup(rows)
    assert all(r["new_url"] == "" and r["status"] == "not_found" for r in out)


def test_apply_dedup_drops_two_unrelated_surnames():
    rows = [
        {"name": "Alice Anderson", "new_url": "https://x.edu/shared-400x400.jpg", "status": "new"},
        {"name": "Bob Baker", "new_url": "https://x.edu/shared-400x400.jpg", "status": "new"},
    ]
    out = mr.apply_dedup(rows)
    assert all(r["new_url"] == "" and r["status"] == "not_found" for r in out)


def test_apply_dedup_keeps_two_related_surnames():
    # Same surname -> likely the same person / name variant -> keep both.
    rows = [
        {"name": "Jon Smith", "new_url": "https://x.edu/smith-400x400.jpg", "status": "new"},
        {"name": "Jonathan Smith", "new_url": "https://x.edu/smith-400x400.jpg", "status": "new"},
    ]
    out = mr.apply_dedup(rows)
    assert all(r["new_url"] and r["status"] == "new" for r in out)


def test_write_outputs_v2_has_only_photo_rows(tmp_path):
    rows = [
        mr.decide_row(_prof("Jane Doe"),
                      {"image_url": "https://x.edu/jane-400x400.jpg",
                       "source_page": "p", "source_tier": "neu",
                       "confidence": "high", "matched_name": "Jane Doe"}),
        mr.decide_row(_prof("No Photo"), None),
    ]
    mr.write_outputs(rows, str(tmp_path))
    with open(tmp_path / "professor_photos_v2.csv", encoding="utf-8") as f:
        out = list(csv.DictReader(f))
    assert [r["name"] for r in out] == ["Jane Doe"]
    assert list(out[0].keys()) == ["name", "image_url", "source_page"]


def test_decide_row_logs_linkedin_even_when_no_photo():
    # Agent confirmed a LinkedIn profile but found no usable photo.
    prof = _prof("Brent Hailpern")
    found = {"image_url": "", "source_page": "", "source_tier": "",
             "confidence": "", "matched_name": "",
             "linkedin_url": "https://www.linkedin.com/in/brenthailpern/"}
    row = mr.decide_row(prof, found)
    assert row["status"] == "not_found"
    assert row["linkedin_url"] == "https://www.linkedin.com/in/brenthailpern/"


def test_decide_row_linkedin_blank_when_absent():
    row = mr.decide_row(_prof("Jane Doe"), None)
    assert row["linkedin_url"] == ""


def _newrow(name, url, matched=None):
    # a verified 'new' audit row as decide_row would produce
    return {"name": name, "new_url": url, "status": "new",
            "matched_name": matched or name}


def test_dedup_keeps_one_when_both_verified_same_url():
    rows = [
        _newrow("Stavros Tripakis", "https://x/tripakis-400x400.jpg"),
        _newrow("Stavros Trypakis", "https://x/tripakis-400x400.jpg"),
    ]
    out = mr.apply_dedup(rows)
    kept = [r for r in out if r["new_url"]]
    dup = [r for r in out if r["status"] == "duplicate"]
    assert len(kept) == 1            # photo preserved on one
    assert len(dup) == 1             # other marked duplicate, not lost-as-wrong
    assert dup[0]["new_url"] == ""


def test_dedup_still_drops_unverified_unrelated_pair():
    # No matched_name => not verified => old collision safeguard applies
    rows = [
        {"name": "Alice Anderson", "new_url": "https://x/shared-400x400.jpg", "status": "new", "matched_name": ""},
        {"name": "Bob Baker", "new_url": "https://x/shared-400x400.jpg", "status": "new", "matched_name": ""},
    ]
    out = mr.apply_dedup(rows)
    assert all(r["new_url"] == "" and r["status"] == "not_found" for r in out)


def test_dedup_drops_three_plus_unverified_shared():
    rows = [
        {"name": "A One", "new_url": "https://x/g.jpg", "status": "new", "matched_name": ""},
        {"name": "B Two", "new_url": "https://x/g.jpg", "status": "new", "matched_name": ""},
        {"name": "C Three", "new_url": "https://x/g.jpg", "status": "new", "matched_name": ""},
    ]
    out = mr.apply_dedup(rows)
    assert all(r["new_url"] == "" and r["status"] == "not_found" for r in out)
