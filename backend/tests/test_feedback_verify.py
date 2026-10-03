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
