import server


def _arg(fn, qs, *args):
    with server.app.test_request_context("/?" + qs):
        return fn(*args)


def test_int_arg_clamps_and_defaults():
    assert _arg(server.int_arg, "limit=0", "limit", 20, 1, 100) == 1
    assert _arg(server.int_arg, "limit=-5", "limit", 20, 1, 100) == 1
    assert _arg(server.int_arg, "limit=999999", "limit", 20, 1, 100) == 100
    assert _arg(server.int_arg, "limit=abc", "limit", 20, 1, 100) == 20
    assert _arg(server.int_arg, "", "limit", 20, 1, 100) == 20


def test_float_arg_rounds_so_near_values_share_a_cache_key():
    assert _arg(server.float_arg, "r=3.0001", "r", 0.0, 0.0, 5.0) == 3.0
    assert _arg(server.float_arg, "r=3.5", "r", 0.0, 0.0, 5.0) == 3.5
    assert _arg(server.float_arg, "r=nan", "r", 0.0, 0.0, 5.0) == 0.0
    assert _arg(server.float_arg, "r=99", "r", 0.0, 0.0, 5.0) == 5.0


def test_search_text_strips_wildcards_and_caps_length():
    assert server.search_text("100%_match") == "100match"
    assert server.search_text("a b c d e f g h") == "a b c d e f"
    assert len(server.search_text("x" * 500)) == server.SEARCH_MAX_CHARS


def test_catalog_limit_zero_is_not_a_500(monkeypatch):
    monkeypatch.setattr(server, "query_one", lambda *a, **k: {"cnt": 0})
    monkeypatch.setattr(server, "query", lambda *a, **k: [])
    monkeypatch.setattr(server, "data_version", lambda: 0)
    client = server.app.test_client()
    for path in ("/api/professors-catalog?limit=0&page=-3", "/api/courses-catalog?limit=0"):
        assert client.get(path).status_code == 200


def test_filtered_whole_catalog_pages_are_not_cached(monkeypatch):
    monkeypatch.setattr(server, "query_one", lambda *a, **k: {"cnt": 0})
    monkeypatch.setattr(server, "query", lambda *a, **k: [])
    monkeypatch.setattr(server, "data_version", lambda: 0)
    server._cache.clear()
    client = server.app.test_client()
    client.get("/api/professors-catalog?limit=10000&minRating=1.5")
    assert not server._cache
    client.get("/api/professors-catalog?limit=10000&page=1&sort=alpha")   # Compare's request
    assert len(server._cache) == 1
    server._cache.clear()
