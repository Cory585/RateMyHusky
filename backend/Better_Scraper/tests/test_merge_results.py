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
