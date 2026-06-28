import os
import pytest

@pytest.fixture
def q_client(monkeypatch):
    os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
    os.environ.setdefault("JWT_SECRET", "test-secret")
    import server
    monkeypatch.setattr(server, "_get_pool",
        lambda: (_ for _ in ()).throw(AssertionError("no DB in test")), raising=False)
    return server.app.test_client()

def test_chat_question_returns_answer(monkeypatch, q_client):
    import server
    monkeypatch.setattr(server, "handle_question",
        lambda q, session_token, ip_hash, deps: (
            {"mode": "question", "answer": "Guha is fair.", "disclaimer": "AI-generated."}, 200
        ))
    resp = q_client.get("/api/chat?q=is+Guha+hard&mode=question",
                        headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["mode"] == "question"
    assert "answer" in data
    assert "disclaimer" in data

def test_chat_question_kill_switch(monkeypatch, q_client):
    import server
    monkeypatch.setattr(server, "handle_question",
        lambda q, session_token, ip_hash, deps: (
            {"mode": "error", "message": "The question feature is temporarily disabled."}, 503
        ))
    monkeypatch.setattr(server, "CHAT_ENABLED", False)
    resp = q_client.get("/api/chat?q=is+Guha+hard&mode=question",
                        headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 503
