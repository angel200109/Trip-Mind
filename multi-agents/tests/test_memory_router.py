"""
Tests for MemoryRouter — intent-based layer selection.

All three memory layers are fully mocked so no external services
(Redis, PostgreSQL, Chroma) are required.
"""
from __future__ import annotations

import sys
import os
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.router import MemoryRouter


def _make_router(
    working_context: dict | None = None,
    short_history: list | None = None,
    preferences: dict | None = None,
    knowledge: str = "some knowledge",
) -> MemoryRouter:
    """Build a MemoryRouter with fully-mocked memory layers."""
    working = MagicMock()
    working.get_context.return_value = working_context if working_context is not None else {"destination": "Tokyo"}

    short_term = MagicMock()
    short_term.get_history = AsyncMock(
        return_value=short_history if short_history is not None else []
    )

    long_term = MagicMock()
    long_term.get_preferences = AsyncMock(
        return_value=preferences if preferences is not None else {"budget": "high"}
    )
    long_term.search_knowledge = AsyncMock(return_value=knowledge)

    return MemoryRouter(
        working_memory=working,
        short_term=short_term,
        long_term=long_term,
    )


class TestMemoryRouter(unittest.IsolatedAsyncioTestCase):
    """Unit tests for MemoryRouter.load_context()"""

    # ── 1. 工作记忆始终加载 ──────────────────────────────────────────────────

    async def test_always_loads_working_memory(self):
        """Working memory context must always appear in the result."""
        router = _make_router(working_context={"key": "value"})
        result = await router.load_context("s1", "u1", "你好")
        self.assertIn("working", result)
        self.assertEqual(result["working"], {"key": "value"})
        router.working_memory.get_context.assert_called_once_with("s1")

    # ── 2 & 3. 短期记忆 ─────────────────────────────────────────────────────

    async def test_loads_short_term_when_exists(self):
        """Short-term history should be included when the session has messages."""
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        router = _make_router(short_history=history)
        result = await router.load_context("s1", "u1", "你好")
        self.assertIn("short_term", result)
        self.assertEqual(result["short_term"], history)

    async def test_skips_short_term_when_empty(self):
        """Short-term key must NOT appear when there is no history."""
        router = _make_router(short_history=[])
        result = await router.load_context("s1", "u1", "你好")
        self.assertNotIn("short_term", result)

    # ── 4. 偏好关键词 ────────────────────────────────────────────────────────

    async def test_loads_preferences_on_keyword(self):
        """Query with a preference keyword should trigger preferences load."""
        router = _make_router(preferences={"style": "quiet"})
        result = await router.load_context("s1", "u1", "我喜欢安静的酒店")
        self.assertIn("preferences", result)
        self.assertEqual(result["preferences"], {"style": "quiet"})
        router.long_term.get_preferences.assert_awaited_once_with("u1")

    # ── 5. 知识库关键词 ──────────────────────────────────────────────────────

    async def test_loads_knowledge_on_keyword(self):
        """Query with a knowledge keyword should trigger knowledge search."""
        router = _make_router(knowledge="西湖是必去景点")
        result = await router.load_context("s1", "u1", "杭州有什么景点推荐")
        self.assertIn("knowledge", result)
        self.assertEqual(result["knowledge"], "西湖是必去景点")
        router.long_term.search_knowledge.assert_awaited_once_with("杭州有什么景点推荐")

    # ── 7. 默认兜底：长查询无关键词加载偏好 ─────────────────────────────────

    async def test_default_loads_preferences_for_long_query(self):
        """A query longer than 5 chars with no matching keywords defaults to loading preferences."""
        # This query has no pref/hist/knowledge keywords, but is > 5 chars
        query = "帮我安排一下行程"
        router = _make_router()
        result = await router.load_context("s1", "u1", query)
        self.assertIn("preferences", result)
        router.long_term.get_preferences.assert_awaited_once_with("u1")

    # ── 7. 多意图 ────────────────────────────────────────────────────────────

    async def test_multiple_intents(self):
        """A query matching knowledge keywords should load knowledge layer."""
        query = "杭州有什么景点推荐"
        router = _make_router()
        result = await router.load_context("s1", "u1", query)
        self.assertIn("knowledge", result)
        router.long_term.search_knowledge.assert_awaited_once_with(query)


if __name__ == "__main__":
    unittest.main()
