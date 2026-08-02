"""
长期记忆 — 聚合 PG 用户数据 + Chroma RAG 知识库

- 用户偏好（PG user_preferences）
- 对话摘要归档（PG conversation_summaries）
- 旅行历史（PG travel_history）
- 知识库检索（Chroma RAG）
"""
from __future__ import annotations

from typing import Any

from db.models import (
    get_preferences as db_get_preferences,
    upsert_preferences as db_upsert_preferences,
    get_travel_history as db_get_travel_history,
    get_user_summaries as db_get_user_summaries,
)
from tools.rag_tool import get_rag_instance


class LongTermMemory:
    """
    长期记忆：聚合 PG 用户数据 + Chroma RAG 知识库。
    - 用户偏好（PG user_preferences）
    - 对话摘要归档（PG conversation_summaries）
    - 旅行历史（PG travel_history）
    - 知识库检索（Chroma RAG）
    """

    async def get_preferences(self, user_id: str) -> dict:
        """获取用户偏好，返回格式化的偏好 dict，不存在返回空 dict"""
        row = await db_get_preferences(user_id)
        if row is None:
            return {}
        result = dict(row)
        result.pop("user_id", None)
        result.pop("updated_at", None)
        return result

    async def update_preferences(self, user_id: str, **fields) -> None:
        """更新用户偏好（代理到 db.models）"""
        await db_upsert_preferences(user_id, **fields)

    async def get_travel_history(self, user_id: str, limit: int = 5) -> list[dict]:
        """获取旅行历史"""
        return await db_get_travel_history(user_id, limit)

    async def get_summaries(self, user_id: str, limit: int = 5) -> list[dict]:
        """获取对话摘要归档"""
        return await db_get_user_summaries(user_id, limit)

    async def search_knowledge(self, query: str, k: int = 3) -> str:
        """检索 RAG 知识库"""
        rag = get_rag_instance()
        if rag.vector_store is None:
            return "知识库未初始化"
        return await rag.search(query, k)

    async def get_user_profile(self, user_id: str) -> dict:
        """聚合获取完整用户画像（偏好 + 最近旅行 + 摘要）"""
        preferences = await self.get_preferences(user_id)
        travel_history = await self.get_travel_history(user_id, limit=3)
        summaries = await self.get_summaries(user_id, limit=3)
        return {
            "preferences": preferences,
            "travel_history": travel_history,
            "summaries": summaries,
        }
