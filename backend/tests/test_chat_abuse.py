"""Unit tests for chat_abuse.py — the strike/cap/ban ladder.

No DB needed: the module takes an injected query_one_fn, so we hand it a fake
that returns a chosen strike count and daily-question count based on which SQL
it is asked to run.
"""
import pytest
from rag.chat_abuse import (
    abuse_check, strike_count, daily_question_count,
    STRIKE_STATUSES, STRIKE_WINDOW_DAYS, _CAPS,
)


def make_query_fn(strikes, today=0, calls=None):
    """Fake query_one_fn: distinguishes the two queries by SQL text."""
    def q(sql, params=None):
        if calls is not None:
            calls.append((sql, params))
        if "result_status = ANY" in sql:
            return {"c": strikes}
        return {"c": today}
    return q


class TestStrikeCount:
    def test_returns_count(self):
        assert strike_count("tok", make_query_fn(4)) == 4

    def test_none_row_defaults_to_zero(self):
        assert strike_count("tok", lambda sql, params=None: None) == 0

    def test_null_count_defaults_to_zero(self):
        assert strike_count("tok", lambda sql, params=None: {"c": None}) == 0

    def test_query_is_time_windowed(self):
        calls = []
        strike_count("tok", make_query_fn(0, calls=calls))
        sql, params = calls[0]
        assert "INTERVAL '1 day'" in sql
        assert STRIKE_WINDOW_DAYS in params
        assert list(STRIKE_STATUSES) in params


class TestDailyQuestionCount:
    def test_returns_count(self):
        assert daily_question_count("tok", make_query_fn(0, today=7)) == 7

    def test_none_row_defaults_to_zero(self):
        assert daily_question_count("tok", lambda sql, params=None: None) == 0

    def test_scoped_to_today_and_questions(self):
        calls = []
        daily_question_count("tok", make_query_fn(0, calls=calls))
        sql, _ = calls[0]
        assert "date_trunc('day', now())" in sql
        assert "mode = 'question'" in sql


class TestAbuseCheck:
    def test_no_strikes_allowed_no_message(self):
        r = abuse_check("tok", make_query_fn(0, 0))
        assert r == {"allowed": True, "banned": False, "message": None}

    @pytest.mark.parametrize("strikes", [1, 2])
    def test_low_strikes_warn_but_allow(self, strikes):
        r = abuse_check("tok", make_query_fn(strikes, 99))
        # warn tier ignores usage entirely — always allowed, always messaged
        assert r["allowed"] is True
        assert r["banned"] is False
        assert r["message"]

    @pytest.mark.parametrize("strikes,cap", sorted(_CAPS.items()))
    def test_capped_tier_allows_under_cap(self, strikes, cap):
        r = abuse_check("tok", make_query_fn(strikes, cap - 1))
        assert r["allowed"] is True
        assert r["banned"] is False

    @pytest.mark.parametrize("strikes,cap", sorted(_CAPS.items()))
    def test_capped_tier_blocks_at_cap(self, strikes, cap):
        r = abuse_check("tok", make_query_fn(strikes, cap))
        assert r["allowed"] is False
        assert r["banned"] is False
        assert r["message"]

    @pytest.mark.parametrize("strikes", [6, 7, 20])
    def test_ban_tier(self, strikes):
        r = abuse_check("tok", make_query_fn(strikes, 0))
        assert r["allowed"] is False
        assert r["banned"] is True
        assert r["message"]

    def test_ban_takes_priority_over_cap(self):
        # 6 strikes with zero usage should still ban, not fall through to the cap logic
        r = abuse_check("tok", make_query_fn(6, 0))
        assert r["banned"] is True
