import datetime

import jwt
import server


def _client():
    return server.app.test_client()


def test_sign_in_sets_a_state_and_sends_it_to_google():
    resp = _client().get("/api/auth/google")
    assert resp.status_code == 302
    state_cookie = [c for c in resp.headers.getlist("Set-Cookie") if c.startswith("auth_state=")]
    assert state_cookie
    state = state_cookie[0].split(";")[0].split("=", 1)[1]
    assert f"state={state}" in resp.headers["Location"]


def test_callback_without_matching_state_is_refused(monkeypatch):
    monkeypatch.setattr(server.http_requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not exchange the code")))
    client = _client()
    resp = client.get("/api/auth/google/callback?code=attacker-code&state=x")
    assert "auth_error=state_mismatch" in resp.headers["Location"]
    client.set_cookie("auth_state", "real-state")
    resp = client.get("/api/auth/google/callback?code=attacker-code&state=other")
    assert "auth_error=state_mismatch" in resp.headers["Location"]


def _token(**claims):
    base = {"sub": "u1", "email": "t@husky.neu.edu", "name": "T",
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)}
    base.update(claims)
    return jwt.encode(base, "test-secret", algorithm="HS256")


def test_session_token_needs_our_issuer_and_audience():
    assert server.decode_session_token(_token(iss=server.JWT_ISSUER, aud=server.JWT_AUDIENCE))
    assert server.decode_session_token(_token()) is None
    assert server.decode_session_token(_token(iss=server.JWT_ISSUER, aud="something-else")) is None
    assert server.decode_session_token(None) is None
    assert server.decode_session_token("not.a.jwt") is None
