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

    working = MagicMock()

    promotion = MemoryPromotion(
        short_term=short_term,
        long_term=long_term,
        working=working,
    )
    return promotion, short_term, long_term, working


class TestMemoryPromotion(unittest.IsolatedAsyncioTestCase):
    """Unit tests for MemoryPromotion.promote() and extraction helpers."""

    # ── 1. save_message called twice with pg_session_id ─────────────────────

    async def test_promote_saves_to_pg(self):
        """With a pg_session_id, save_message must be called twice (user + assistant)."""
        promotion, _, _, _ = _make_promotion()
        pg_session_id = uuid.uuid4()

        with patch("memory.promotion.models") as mock_models:
            mock_models.save_message = AsyncMock()
            mock_models.save_travel_history = AsyncMock()

            result = await promotion.promote(
                session_id="s1",
                user_id="u1",
                user_message="你好",
                assistant_response="你好！有什么可以帮您的？",
                pg_session_id=pg_session_id,
            )

        assert result["saved_to_pg"] is True
        assert mock_models.save_message.call_count == 2
        calls = mock_models.save_message.call_args_list
        assert calls[0].args == (pg_session_id, "user", "你好")
        assert calls[1].args == (pg_session_id, "assistant", "你好！有什么可以帮您的？")

    # ── 2. add_message called for both roles ─────────────────────────────────

    async def test_promote_saves_to_short_term(self):
        """add_message must be called for both user and assistant messages."""
        promotion, short_term, _, _ = _make_promotion()

        with patch("memory.promotion.models") as mock_models:
            mock_models.save_message = AsyncMock()
            mock_models.save_travel_history = AsyncMock()

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

    # ── 3. no pg_session_id → save_message NOT called ────────────────────────

    async def test_promote_without_pg_session(self):
        """Without pg_session_id, save_message must NOT be called."""
        promotion, _, _, _ = _make_promotion()

        with patch("memory.promotion.models") as mock_models:
            mock_models.save_message = AsyncMock()
            mock_models.save_travel_history = AsyncMock()

            result = await promotion.promote(
                session_id="s1",
                user_id="u1",
                user_message="你好",
                assistant_response="你好！",
                pg_session_id=None,
            )

        assert result["saved_to_pg"] is False
        mock_models.save_message.assert_not_called()

    # ── 4. budget extraction ─────────────────────────────────────────────────

    def test_extract_preferences_budget(self):
        """'预算5000' should map to budget_level = '高端型'."""
        promotion, _, _, _ = _make_promotion()
        fields = promotion._extract_preferences("预算5000去旅游", "")
        assert fields.get("budget_level") == "高端型"

    # ── 5. liked_activities extraction ───────────────────────────────────────

    def test_extract_preferences_likes(self):
        """'我喜欢爬山' should produce liked_activities = ['爬山']."""
        promotion, _, _, _ = _make_promotion()
        fields = promotion._extract_preferences("我喜欢爬山", "")
        assert fields.get("liked_activities") == ["爬山"]

    # ── 6. disliked_activities extraction ────────────────────────────────────

    def test_extract_preferences_dislikes(self):
        """'我不喜欢购物' should produce disliked_activities = ['购物']."""
        promotion, _, _, _ = _make_promotion()
        fields = promotion._extract_preferences("我不喜欢购物", "")
        assert fields.get("disliked_activities") == ["购物"]

    # ── 7. travel info extraction with plan indicators ────────────────────────

    def test_extract_travel_info(self):
        """User '去杭州3天预算3000', assistant with '行程安排' → correct extraction."""
        promotion, _, _, _ = _make_promotion()
        info = promotion._extract_travel_info(
            "我想去杭州3天预算3000",
            "好的，以下是您的行程安排：第一天游西湖……",
        )
        assert info.get("destination") == "杭州"
        assert info.get("travel_days") == 3
        assert info.get("budget") == 3000.0
        assert info.get("status") == "planned"

    # ── 8. no plan indicators → empty dict ───────────────────────────────────

    def test_extract_travel_info_no_plan_in_response(self):
        """When assistant response has no plan indicators, return empty dict."""
        promotion, _, _, _ = _make_promotion()
        info = promotion._extract_travel_info(
            "我想去杭州3天",
            "好的，我明白了，请告诉我更多需求。",
        )
        assert info == {}

    # ── 9. full pipeline — all flags True ────────────────────────────────────

    async def test_promote_full_pipeline(self):
        """All detections trigger → all result flags should be True."""
        promotion, short_term, long_term, _ = _make_promotion()
        pg_session_id = uuid.uuid4()

        with patch("memory.promotion.models") as mock_models:
            mock_models.save_message = AsyncMock()
            mock_models.save_travel_history = AsyncMock()

            result = await promotion.promote(
                session_id="s1",
                user_id="u1",
                user_message="我喜欢爬山，预算5000，想去杭州3天",
                assistant_response="好的，以下是您的行程安排：第一天游西湖……",
                pg_session_id=pg_session_id,
            )

        assert result["saved_to_pg"] is True
        assert result["saved_to_short_term"] is True
        assert result["preferences_updated"] is True
        assert result["travel_history_saved"] is True

        # Verify the downstream calls happened
        assert mock_models.save_message.call_count == 2
        assert short_term.add_message.call_count == 2
        long_term.update_preferences.assert_awaited_once()
        mock_models.save_travel_history.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
