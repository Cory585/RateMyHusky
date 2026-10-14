from server import professor_verify_note


def test_other_types_get_no_note():
    assert professor_verify_note("bug", "a@northeastern.edu") is None
    assert professor_verify_note("banappeal", "") is None
    # Student account deletion (Ask data, bookmarks) never touches a professor page.
    assert professor_verify_note("datadeletion", "") is None


def test_missing_email_cannot_be_verified():
    for t in ("missing", "incorrectdata", "professor"):
        assert "can't be verified" in professor_verify_note(t, "")


def test_northeastern_address_is_confirmed_by_reply():
    for email in ("J.Smith@Northeastern.edu", "j.smith@khoury.northeastern.edu"):
        assert "reply to confirm" in professor_verify_note("professor", email)


def test_other_address_is_asked_for_a_northeastern_one():
    for email in ("prof@gmail.com", "student@husky.neu.edu", "x@northeastern.edu.evil.com",
                  "x@evilnortheastern.edu"):
        assert "not a Northeastern address" in professor_verify_note("incorrectdata", email)


def test_professor_requests_require_an_email(monkeypatch):
    import server
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    resp = server.app.test_client().post(
        "/api/feedback", json={"feedbackType": "professor", "description": "remove my page"})
    assert resp.status_code == 400
    assert "Email is required" in resp.get_json()["error"]


def _post(monkeypatch, **body):
    import server
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    # Unset so a request that passes validation stops at "not configured" instead of emailing.
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr(server.limiter, "enabled", False)
    return server.app.test_client().post("/api/feedback", json=body)


def test_unknown_types_are_rejected(monkeypatch):
    resp = _post(monkeypatch, feedbackType="Buy cheap watches", description="hi")
    assert resp.status_code == 400
    assert "Unknown feedback type" in resp.get_json()["error"]


def test_retired_and_merged_types_still_pass_validation(monkeypatch):
    # Cached bundles and the maintenance page can still send the old keys.
    for t in ("bug", "feature", "missing", "incorrectdata", "general"):
        resp = _post(monkeypatch, feedbackType=t, description="hi")
        assert resp.get_json()["error"] == "Email service not configured", t
    for t in ("banappeal", "datadeletion", "professor"):
        resp = _post(monkeypatch, feedbackType=t, description="hi", email="a@northeastern.edu")
        assert resp.get_json()["error"] == "Email service not configured", t


def test_overlong_description_is_rejected(monkeypatch):
    import server
    resp = _post(monkeypatch, feedbackType="bug", description="x" * (server.FEEDBACK_MAX_DESCRIPTION + 1))
    assert resp.status_code == 400
    assert "too long" in resp.get_json()["error"]


def test_overlong_email_is_rejected(monkeypatch):
    resp = _post(monkeypatch, feedbackType="bug", description="hi", email="a" * 250 + "@x.com")
    assert resp.status_code == 400
    assert "Invalid email" in resp.get_json()["error"]
