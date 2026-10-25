"""PREVIEW_ORIGIN_RE: Vercel Preview origins pass the origin check and keep login on the preview."""

import re

import pytest

PREVIEW = "https://rate-my-husky-abc123-grain-is-grainys-projects.vercel.app"
PATTERN = r"^https://rate-my-husky-[a-z0-9-]+-grain-is-grainys-projects\.vercel\.app$"


@pytest.fixture
def server_mod(monkeypatch):
    import server
    monkeypatch.setattr(server, "_get_pool", lambda: (_ for _ in ()).throw(AssertionError("no DB in test")), raising=False)
    monkeypatch.setattr(server, "FRONTEND_URL", "https://ratemyhusky.com")
    monkeypatch.setattr(server, "PREVIEW_ORIGIN_RE", re.compile(PATTERN))
    return server


@pytest.mark.parametrize("referer", [PREVIEW + "/professors", "http://localhost:5173/"])
def test_allowed_origins_pass(server_mod, referer):
    with server_mod.app.test_request_context("/api/professors", headers={"Referer": referer}):
        assert server_mod.check_origin() is None


@pytest.mark.parametrize("referer", [
    "https://rate-my-husky-abc123-someone-else.vercel.app/",
    "https://evil.example/" + PREVIEW,
    PREVIEW + ".evil.example/",
    "http://rate-my-husky-abc123-grain-is-grainys-projects.vercel.app/",
])
def test_lookalike_origins_blocked(server_mod, referer):
    with server_mod.app.test_request_context("/api/professors", headers={"Referer": referer}):
        resp = server_mod.check_origin()
        assert resp is not None and resp[1] == 403


def test_unset_pattern_blocks_previews(server_mod, monkeypatch):
    monkeypatch.setattr(server_mod, "PREVIEW_ORIGIN_RE", None)
    with server_mod.app.test_request_context("/api/professors", headers={"Referer": PREVIEW + "/"}):
        resp = server_mod.check_origin()
        assert resp is not None and resp[1] == 403


def test_login_from_preview_uses_preview_redirect(server_mod):
    client = server_mod.app.test_client()
    resp = client.get("/api/auth/google?popup=1", headers={"Referer": PREVIEW + "/courses"})
    assert resp.status_code == 302
    assert "redirect_uri=" + "https%3A%2F%2Frate-my-husky-abc123" in resp.headers["Location"]
    assert any(c.startswith("auth_origin=" + PREVIEW) for c in resp.headers.getlist("Set-Cookie"))


def test_login_from_prod_unchanged(server_mod):
    client = server_mod.app.test_client()
    resp = client.get("/api/auth/google?popup=1", headers={"Referer": "https://ratemyhusky.com/"})
    assert "redirect_uri=https%3A%2F%2Fratemyhusky.com%2Fapi" in resp.headers["Location"]
    assert not any(c.startswith("auth_origin=") for c in resp.headers.getlist("Set-Cookie"))


@pytest.mark.parametrize("cookie,expected", [
    (PREVIEW, PREVIEW),
    ("https://evil.example", "https://ratemyhusky.com"),
    ("", "https://ratemyhusky.com"),
])
def test_callback_rechecks_cookie(server_mod, cookie, expected):
    headers = {"Cookie": f"auth_origin={cookie}"} if cookie else {}
    with server_mod.app.test_request_context("/api/auth/google/callback", headers=headers):
        assert server_mod._callback_frontend() == expected
