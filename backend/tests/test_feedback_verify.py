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


def test_every_accepted_type_passes_validation(monkeypatch):
    # Includes the merged "missing"/"feature" keys that cached bundles can still send.
    for t in ("bug", "feature", "missing", "incorrectdata", "general"):
        resp = _post(monkeypatch, feedbackType=t, description="hi")
        assert resp.get_json()["error"] == "Email service not configured", t
    for t in ("banappeal", "datadeletion", "professor"):
        resp = _post(monkeypatch, feedbackType=t, description="hi", email="a@northeastern.edu",
                     accountSub=_account_token())
        assert resp.get_json()["error"] == "Email service not configured", t


def _account_token():
    import jwt
    import server
    return jwt.encode({"sub": "user-123"}, server.JWT_SECRET, algorithm="HS256")


def test_data_deletion_requires_a_valid_account(monkeypatch):
    for token in (None, "not-a-jwt"):
        resp = _post(monkeypatch, feedbackType="datadeletion", description="hi",
                     email="a@northeastern.edu", accountSub=token)
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "Sign in required"


def test_invalid_requests_are_rejected_before_captcha(monkeypatch):
    import server
    monkeypatch.setattr(server.limiter, "enabled", False)
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    def no_siteverify(*a, **kw):
        raise AssertionError("siteverify called for an invalid request")
    monkeypatch.setattr(server.http_requests, "post", no_siteverify)
    resp = server.app.test_client().post("/api/feedback", json={
        "feedbackType": "bug", "description": "x" * (server.FEEDBACK_MAX_DESCRIPTION + 1),
        "turnstileToken": "tok"})
    assert resp.status_code == 400
    assert "too long" in resp.get_json()["error"]


def test_non_string_fields_are_rejected_not_crashed(monkeypatch):
    resp = _post(monkeypatch, feedbackType=123, description="hi")
    assert resp.status_code == 400
    resp = _post(monkeypatch, feedbackType="bug", description="hi", email=None)
    assert resp.get_json()["error"] == "Email service not configured"


def test_overlong_description_is_rejected(monkeypatch):
    import server
    resp = _post(monkeypatch, feedbackType="bug", description="x" * (server.FEEDBACK_MAX_DESCRIPTION + 1))
    assert resp.status_code == 400
    assert "too long" in resp.get_json()["error"]


def test_overlong_email_is_rejected(monkeypatch):
    resp = _post(monkeypatch, feedbackType="bug", description="hi", email="a" * 250 + "@x.com")
    assert resp.status_code == 400
    assert "Invalid email" in resp.get_json()["error"]


def test_daily_cap_leaves_resend_free_tier_headroom(monkeypatch):
    # Resend's free tier sends 100 a day across feedback and usage alerts; stop feedback at 80.
    import server
    from datetime import datetime, timezone
    monkeypatch.setattr(server, "_feedback_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    monkeypatch.setattr(server, "_feedback_count", 79)
    assert _post(monkeypatch, feedbackType="bug", description="hi").status_code == 500  # the 80th, not configured
    resp = _post(monkeypatch, feedbackType="bug", description="hi")
    assert resp.status_code == 429 and "Daily feedback limit" in resp.get_json()["error"]


def test_signed_in_requests_get_past_a_full_anonymous_cap(monkeypatch):
    # 80 anonymous reports cost an attacker about $0.12 in CAPTCHA solves; they mustn't
    # block a data-deletion request. A ban appeal with no verified account shares the cap.
    import server
    from datetime import datetime, timezone
    monkeypatch.setattr(server, "_feedback_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    monkeypatch.setattr(server, "_feedback_count", 80)
    monkeypatch.setattr(server, "_account_feedback_count", 0, raising=False)
    for t in ("datadeletion", "banappeal"):
        resp = _post(monkeypatch, feedbackType=t, description="hi", email="a@northeastern.edu",
                     accountSub=_account_token())
        assert resp.get_json()["error"] == "Email service not configured", t
    resp = _post(monkeypatch, feedbackType="banappeal", description="hi", email="a@northeastern.edu")
    assert resp.status_code == 429


def test_signed_in_allowance_has_its_own_cap(monkeypatch):
    import server
    from datetime import datetime, timezone
    monkeypatch.setattr(server, "_feedback_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    monkeypatch.setattr(server, "_feedback_count", 0)
    monkeypatch.setattr(server, "_account_feedback_count", server.ACCOUNT_FEEDBACK_DAILY_LIMIT - 1, raising=False)
    body = dict(feedbackType="datadeletion", description="hi", email="a@northeastern.edu",
                accountSub=_account_token())
    assert _post(monkeypatch, **body).status_code == 500  # the last one, not configured
    resp = _post(monkeypatch, **body)
    assert resp.status_code == 429 and "Daily feedback limit" in resp.get_json()["error"]
    # Resend's free 100/day also covers up to 3 usage alerts and the daily review digest.
    assert server.FEEDBACK_DAILY_LIMIT + server.ACCOUNT_FEEDBACK_DAILY_LIMIT + 3 + 1 <= 100
