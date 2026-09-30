"""drop_trace_data against a recording fake cursor. No database, no network."""

import drop_trace_data as d


class FakeConn:
    def __init__(self, tables, trace_evidence=0, embeddings=0):
        self.tables = set(tables)
        self.trace_evidence = trace_evidence
        self.embeddings = embeddings
        self.executed = []
        self.commits = 0
        self._rows = []

    def cursor(self):
        return self

    def commit(self):
        self.commits += 1

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.executed.append(flat)
        if "information_schema.tables" in flat:
            self._rows = [(1 if params[0] in self.tables else 0,)]
        elif flat.startswith("SELECT count(*) FROM evidence_embeddings"):
            self._rows = [(self.embeddings,)]
        elif flat.startswith("SELECT count(*) FROM evidence "):
            self._rows = [(self.trace_evidence,)]
        elif flat.startswith("SELECT count(*) FROM"):
            self._rows = [(7,)]
        elif flat.startswith("SELECT id FROM evidence"):
            n = min(params[0], self.trace_evidence)
            self._rows = [(i,) for i in range(n)]
            self.trace_evidence -= n
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)


ALL = {"trace_courses", "trace_scores", "trace_comments", "evidence", "evidence_embeddings"}


def test_dry_run_changes_nothing(capsys):
    conn = FakeConn(ALL, trace_evidence=12, embeddings=12)
    report = d.drop_trace_data(conn, execute=False)
    assert report["trace_courses"] == 7
    assert report["evidence"] == 12
    assert not any(s.startswith(("DROP", "DELETE")) for s in conn.executed)
    assert conn.commits == 0
    assert "Dry run" in capsys.readouterr().out


def test_execute_deletes_in_batches_then_drops_tables(monkeypatch):
    monkeypatch.setattr(d, "BATCH", 5)
    conn = FakeConn(ALL, trace_evidence=12, embeddings=12)
    d.drop_trace_data(conn, execute=True)
    deletes = [s for s in conn.executed if s.startswith("DELETE")]
    # 12 rows in batches of 5 = 3 batches, embeddings before evidence in each.
    assert len(deletes) == 6
    assert deletes[0].startswith("DELETE FROM evidence_embeddings")
    assert deletes[1].startswith("DELETE FROM evidence ")
    drops = [s for s in conn.executed if s.startswith("DROP TABLE")]
    assert sorted(drops) == sorted(f"DROP TABLE IF EXISTS {t} CASCADE" for t in d.TRACE_TABLES)
    # The tables go only after the evidence rows are gone.
    assert conn.executed.index(drops[0]) > conn.executed.index(deletes[-1])


def test_absent_tables_are_skipped():
    conn = FakeConn({"evidence"}, trace_evidence=0)
    report = d.drop_trace_data(conn, execute=True)
    assert report["trace_courses"] is None
    assert not any(s.startswith("DROP") for s in conn.executed)


def test_no_evidence_table_is_fine():
    conn = FakeConn({"trace_comments"})
    report = d.drop_trace_data(conn, execute=True)
    assert report["evidence"] == 0
    assert "DROP TABLE IF EXISTS trace_comments CASCADE" in conn.executed
