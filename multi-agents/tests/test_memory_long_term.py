"""测试 memory/long_term.py 长期记忆"""
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.long_term import LongTermMemory


@pytest.fixture
def memory():
    return LongTermMemory()


# ---------------------------------------------------------------------------
# get_preferences
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_preferences_exists(memory):
    """mock db 返回数据时，应去掉 user_id 和 updated_at 后返回"""
    fake_row = {
        "user_id": "u1",
        "updated_at": "2024-01-01",
        "preferred_style": "自由行",
        "budget_range": "中等",
    }
    with patch("memory.long_term.db_get_preferences", new=AsyncMock(return_value=fake_row)):
        result = await memory.get_preferences("u1")

    assert "user_id" not in result
    assert "updated_at" not in result
    assert result["preferred_style"] == "自由行"
    assert result["budget_range"] == "中等"


@pytest.mark.asyncio
async def test_get_preferences_not_exists(memory):
    """mock db 返回 None 时，应返回空 dict"""
    with patch("memory.long_term.db_get_preferences", new=AsyncMock(return_value=None)):
        result = await memory.get_preferences("u_unknown")

    assert result == {}


# ---------------------------------------------------------------------------
# update_preferences
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_preferences(memory):
    """应将参数原样代理给 db.models.upsert_preferences"""
    mock_upsert = AsyncMock()
    with patch("memory.long_term.db_upsert_preferences", new=mock_upsert):
        await memory.update_preferences("u1", preferred_style="跟团", budget_range="高")

    mock_upsert.assert_awaited_once_with("u1", preferred_style="跟团", budget_range="高")


# ---------------------------------------------------------------------------
# get_summaries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_summaries(memory):
    """应将结果原样透传"""
    fake_summaries = [
        {"id": 10, "summary": "用户计划去北京旅行三天"},
        {"id": 11, "summary": "讨论了预算和景点"},
    ]
    with patch("memory.long_term.db_get_user_summaries", new=AsyncMock(return_value=fake_summaries)):
        result = await memory.get_summaries("u1", limit=5)

    assert result == fake_summaries


# ---------------------------------------------------------------------------
# search_knowledge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_knowledge(memory):
    """RAG 正常时应返回 rag.search() 的结果"""
    mock_rag = MagicMock()
    mock_rag.vector_store = MagicMock()  # non-None => initialized
    mock_rag.search = AsyncMock(return_value="北京旅游攻略：故宫、长城...")

    with patch("memory.long_term.get_rag_instance", return_value=mock_rag):
        result = await memory.search_knowledge("北京景点", k=3)

    mock_rag.search.assert_awaited_once_with("北京景点", 3)
    assert result == "北京旅游攻略：故宫、长城..."


@pytest.mark.asyncio
async def test_search_knowledge_not_initialized(memory):
    """RAG vector_store 为 None 时，应返回 '知识库未初始化'"""
    mock_rag = MagicMock()
    mock_rag.vector_store = None

    with patch("memory.long_term.get_rag_instance", return_value=mock_rag):
        result = await memory.search_knowledge("北京景点", k=3)

    assert result == "知识库未初始化"


# ---------------------------------------------------------------------------
# get_user_profile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_user_profile(memory):
    """应聚合偏好、摘要到同一个 dict"""
    fake_prefs = {"preferred_style": "自由行"}
    fake_summaries = [{"summary": "计划了成都旅行"}]

    with (
        patch("memory.long_term.db_get_preferences", new=AsyncMock(return_value={**fake_prefs, "user_id": "u1", "updated_at": "x"})),
        patch("memory.long_term.db_get_user_summaries", new=AsyncMock(return_value=fake_summaries)),
    ):
        profile = await memory.get_user_profile("u1")

    assert "preferences" in profile
    assert "summaries" in profile
    assert profile["preferences"]["preferred_style"] == "自由行"
    assert profile["summaries"] == fake_summaries
