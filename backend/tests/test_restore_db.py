"""restore_db: section filtering, TRUNCATE injection, and never a partial commit."""

import gzip
import os
import time

import pytest

import restore_db

LINES = restore_db._FIXTURE_SQL.splitlines(keepends=True)


def test_selftest_passes():
    assert restore_db.selftest() == 0


def test_dump_tables_lists_data_sections_in_order():
    assert restore_db.dump_tables(LINES) == ["rmp_reviews", "stats_cache"]


def test_sequence_sections_belong_to_their_table():
    assert restore_db.section_table("SEQUENCE SET", "rmp_reviews_id_seq") == "rmp_reviews"
    assert restore_db.section_table("SEQUENCE SET", "prof_rmp_link_rowid_seq") == "prof_rmp_link"
    assert restore_db.section_table("TABLE DATA", "stats_cache") == "stats_cache"


def test_filter_keeps_selected_sections_and_injects_one_truncate():
    out = "".join(restore_db.filter_dump(LINES, {"rmp_reviews"}))
    assert out.count('TRUNCATE public."rmp_reviews";') == 1
    assert out.index("TRUNCATE") < out.index("INSERT INTO public.rmp_reviews")
    assert "setval('public.rmp_reviews_id_seq'" in out
    assert "stats_cache" not in out
    assert "\\restrict abc123" in out and "\\unrestrict abc123" in out
    assert "tricky; has ''quoted'' text;\nand a newline" in out


def test_evidence_pulls_in_its_embeddings():
    assert restore_db.expand_tables({"evidence"}) == {"evidence", "evidence_embeddings"}
    assert restore_db.truncate_sql({"evidence_embeddings", "evidence"}) == \
        'TRUNCATE public."evidence", public."evidence_embeddings";\n'


def test_pre_postgres_dumps_are_rejected(tmp_path):
    old = tmp_path / "old.sql.gz"
    with gzip.open(old, "wt", encoding="utf-8") as f:
        f.write("-- ratemyhusky NEW cluster backup\nDROP TABLE IF EXISTS rmp_reviews CASCADE;\n")
    with pytest.raises(ValueError, match="not a pg_dump"):
        restore_db.check_format(str(old))


def test_newest_dump_is_by_mtime_not_name(tmp_path):
    old = tmp_path / "ratemyhusky_new_20260602T001500Z.sql.gz"   # sorts last by name
    new = tmp_path / "ratemyhusky_20261010T120000Z.sql.gz"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - 86400
    os.utime(old, (past, past))
    assert restore_db._newest_dump(str(tmp_path)) == str(new)


def test_corrupt_dump_kills_psql_instead_of_committing(tmp_path, monkeypatch):
    path = tmp_path / "corrupt.sql.gz"
    good = gzip.compress(restore_db._FIXTURE_SQL.encode() * 200)
    path.write_bytes(good[: len(good) // 2])          # truncated gzip stream

    class FakeStdin:
        def __init__(self):
            self.closed = False

        def write(self, s):
            pass

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self, *a, **k):
            self.stdin = FakeStdin()
            self.killed_while_open = None
            self.returncode = 0
            procs.append(self)

        def kill(self):
            self.killed_while_open = not self.stdin.closed

        def wait(self):
            return self.returncode

    procs = []
    monkeypatch.setattr(restore_db.subprocess, "Popen", FakeProc)
    with pytest.raises((EOFError, OSError)):
        restore_db.restore("postgresql://x", str(path), {"rmp_reviews"})
    assert procs[0].killed_while_open is True


def test_confirm_label_for_hostnames_and_ips():
    assert restore_db.confirm("db.example.com", {"t"}, "d", input_fn=lambda _: "db") is True
    assert restore_db.confirm("db.example.com", {"t"}, "d", input_fn=lambda _: "yes") is False
    assert restore_db.confirm("129.80.1.2", {"t"}, "d", input_fn=lambda _: "129.80.1.2") is True
    assert restore_db.confirm("h", {"t"}, "d",
                              input_fn=lambda _: (_ for _ in ()).throw(AssertionError("prompted")),
                              assume_yes=True) is True


def test_filter_passes_carriage_returns_through_unchanged():
    lines = ["-- Data for Name: t; Type: TABLE DATA; Schema: public; Owner: -\r\n",
             "INSERT INTO public.t (c) VALUES\r\n",
             "\t('a\r\nb\rc');\r\n"]
    out = "".join(restore_db.filter_dump(lines, {"t"}))
    assert "'a\r\nb\rc'" in out
    assert out.endswith("INSERT INTO public.t (c) VALUES\r\n\t('a\r\nb\rc');\r\n")
    assert restore_db.dump_tables(lines) == ["t"]


def test_restore_sends_bytes_to_psql_without_newline_translation(tmp_path, monkeypatch):
    dump = ("-- PostgreSQL database dump\n\n"
            "-- Data for Name: t; Type: TABLE DATA; Schema: public; Owner: -\n\n"
            "INSERT INTO public.t (c) VALUES\n\t('a\r\nb\rc');\n\n"
            "-- PostgreSQL database dump complete\n")
    path = tmp_path / "crlf.sql.gz"
    path.write_bytes(gzip.compress(dump.encode("utf-8")))

    class FakeStdin:
        def __init__(self):
            self.data = b""
            self.closed = False

        def write(self, b):
            assert isinstance(b, bytes)
            self.data += b

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self, *a, **k):
            self.stdin = FakeStdin()
            self.killed = False
            self.returncode = 0
            procs.append(self)

        def kill(self):
            self.killed = True

        def wait(self):
            return self.returncode

    procs = []
    monkeypatch.setattr(restore_db.subprocess, "Popen", FakeProc)
    restore_db.restore("postgresql://x", str(path), {"t"})
    sent = procs[0].stdin.data
    assert b"('a\r\nb\rc');\n" in sent
    assert b"\n" in sent and sent.count(b"\r") == 2      # only the two inside the literal
    assert b"VALUES\n\t(" in sent
    assert procs[0].stdin.closed and not procs[0].killed
