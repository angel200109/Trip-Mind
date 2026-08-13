"""
Tests for MemoryPromotion — write-back pipeline after each request.

All external dependencies (db.models, short_term, long_term) are mocked
so no Redis, PostgreSQL, or network is required.
"""
from __future__ import annotations

import sys
import os
import uuid
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.promotion import MemoryPromotion


def _make_promotion() -> tuple[MemoryPromotion, MagicMock, MagicMock, MagicMock]:
    """Build a MemoryPromotion with fully-mocked memory layers."""
    short_term = MagicMock()
    short_term.add_message = AsyncMock()

    long_term = MagicMock()
    long_term.update_preferences = AsyncMock()
    long_term.get_preferences = AsyncMock(return_value={})

    working = MagicMock()

    promotion = MemoryPromotion(
        short_term=short_term,
        long_term=long_term,
        working=working,
    )
    return promotion, short_term, long_term, working


class TestMemoryPromotion(unittest.IsolatedAsyncioTestCase):
    """Unit tests for MemoryPromotion.promote() and extraction helpers."""

    # ── 1. add_message called for both roles ─────────────────────────────────

    async def test_promote_saves_to_short_term(self):
        """add_message must be called for both user and assistant messages."""
        promotion, short_term, _, _ = _make_promotion()

        result = await promotion.promote(
            session_id="s1",
            user_id="u1",
            user_message="帮我规划行程",
            assistant_response="好的，请问目的地是哪里？",
        )

        assert result["saved_to_short_term"] is True
        assert short_term.add_message.call_count == 2
        calls = short_term.add_message.call_args_list
        assert calls[0].args == ("s1", "user", "帮我规划行程")
        assert calls[1].args == ("s1", "assistant", "好的，请问目的地是哪里？")

    # ── 2. budget extraction ─────────────────────────────────────────────────

    def test_extract_preferences_budget(self):
        """'预算5000' should map to budget_level = '高端型'."""
        promotion, _, _, _ = _make_promotion()
        fields = promotion._extract_preferences("预算5000去旅游", "")
        assert fields.get("budget_level") == "高端型"

    # ── 3. liked_activities extraction ───────────────────────────────────────

    def test_extract_preferences_likes(self):
        """'我喜欢爬山' should produce liked_activities = ['爬山']."""
        promotion, _, _, _ = _make_promotion()
        fields = promotion._extract_preferences("我喜欢爬山", "")
        assert fields.get("liked_activities") == ["爬山"]

    # ── 4. disliked_activities extraction ────────────────────────────────────

    def test_extract_preferences_dislikes(self):
        """'我不喜欢购物' should produce disliked_activities = ['购物']."""
        promotion, _, _, _ = _make_promotion()
        fields = promotion._extract_preferences("我不喜欢购物", "")
        assert fields.get("disliked_activities") == ["购物"]

    # ── 5. full pipeline — preferences detected ──────────────────────────────

    async def test_promote_full_pipeline(self):
        """Preferences detected → preferences_updated should be True."""
        promotion, short_term, long_term, _ = _make_promotion()

        result = await promotion.promote(
            session_id="s1",
            user_id="u1",
            user_message="我喜欢爬山，预算5000",
            assistant_response="好的，我记住了您的偏好。",
        )

        assert result["saved_to_short_term"] is True
        assert result["preferences_updated"] is True
        assert short_term.add_message.call_count == 2
        long_term.update_preferences.assert_awaited_once()

    # ── 6. no preferences detected → preferences_updated False ───────────────

    async def test_promote_no_preferences(self):
        """When no preferences are detected, preferences_updated = False."""
        promotion, _, long_term, _ = _make_promotion()

        result = await promotion.promote(
            session_id="s1",
            user_id="u1",
            user_message="你好",
            assistant_response="你好！有什么可以帮您的？",
        )

        assert result["preferences_updated"] is False
        long_term.update_preferences.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
