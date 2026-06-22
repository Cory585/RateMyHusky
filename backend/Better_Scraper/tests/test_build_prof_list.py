import os, sys, csv, json
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import build_prof_list as bpl


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header)
        for r in rows:
            w.writerow(r)


def test_build_records_dedup_college_and_existing(tmp_path):
    d = tmp_path
    _write_csv(d / "rmp_professors.csv",
               ["name", "department", "rating", "num_ratings",
                "would_take_again_pct", "level_of_difficulty", "professor_url"],
               [["Jane Doe", "Computer Science", "4", "10", "80%", "2", "u"]])
    _write_csv(d / "trace_courses.csv",
               ["instructorFirstName", "instructorLastName", "departmentName"],
               [["Jane", "Doe", "Computer Science"],      # dup of RMP
                ["John", "Smith", "Economics"]])
    _write_csv(d / "professor_photos.csv",
               ["name", "image_url", "source_page"],
               [["Jane Doe", "https://x.edu/jane-400x400.jpg", "p"]])

    recs = bpl.build_records(str(d))
    by_name = {r["name"].lower(): r for r in recs}
    assert len(recs) == 2                                   # Jane deduped
    assert by_name["jane doe"]["college"] == "Khoury"
    assert by_name["jane doe"]["existing_url"] == "https://x.edu/jane-400x400.jpg"
    assert by_name["john smith"]["college"] == "CSSH"
    assert by_name["john smith"]["existing_url"] == ""
