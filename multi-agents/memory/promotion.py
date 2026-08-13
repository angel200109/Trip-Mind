"""
记忆提升 — 请求结束后决定哪些信息写入短期/长期记忆。

写回 pipeline:
1. 用户消息 + 助手回复 → Redis 短期记忆（热缓存）
2. 检测偏好变化（正则快路径 → LLM 兜底）→ 更新 PG user_preferences
（聊天消息由 chat_service 统一持久化，promote 不重复保存）
"""
from __future__ import annotations

import asyncio
import re
import uuid
from typing import Optional

from memory.short_term import ShortTermMemory
from memory.long_term import LongTermMemory
from memory.working import WorkingMemory
from memory.preference_extractor import extract_preferences_with_llm
from db import models


class MemoryPromotion:
    """
    记忆提升：请求结束后决定哪些信息写入短期/长期记忆。

    写回 pipeline:
    1. 用户消息 + 助手回复 → Redis 短期记忆（热缓存）
    2. 检测偏好变化（正则快路径 → LLM 兜底，5s 超时）→ 更新 PG user_preferences
    """

    def __init__(
        self,
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
        working: WorkingMemory,
    ):
        self.short_term = short_term
        self.long_term = long_term
        self.working = working

    async def promote(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
        assistant_response: str,
        pg_session_id: Optional[uuid.UUID] = None,
    ) -> dict:
        """
        执行记忆提升 pipeline。

        Returns: dict with keys indicating what was promoted:
            {
                "saved_to_short_term": bool,
                "preferences_updated": bool,
            }
        """
        result = {
            "saved_to_short_term": False,
            "preferences_updated": False,
        }

        # 说明：聊天消息（user/assistant）不在这里保存——
        # 由 chat_service 统一负责（流式开始存 user、结束存 assistant），避免重复。

        # 1. 保存到短期记忆（Redis）
        await self.short_term.add_message(session_id, "user", user_message)
        await self.short_term.add_message(session_id, "assistant", assistant_response)
        result["saved_to_short_term"] = True

        # 2. 检测并提取偏好（正则快路径 → LLM 兜底，带超时不阻塞流式）
        preferences = self._extract_preferences(user_message, assistant_response)
        if not preferences:
            try:
                current_prefs = await self.long_term.get_preferences(user_id) or {}
                preferences = await asyncio.wait_for(
                    extract_preferences_with_llm(
                        user_message, assistant_response, current_prefs
                    ),
                    timeout=5.0,
                )
                if preferences:
                    print(f"  [LLM] 提取到偏好: {preferences}")
            except asyncio.TimeoutError:
                print("  [WARN] LLM 偏好提取超时，跳过")
            except Exception as e:
                print(f"  [WARN] LLM 偏好提取失败: {e}")

        if preferences:
            await self.long_term.update_preferences(user_id, **preferences)
            result["preferences_updated"] = True

        return result

    def _extract_preferences(self, user_msg: str, assistant_msg: str) -> dict:
        """
        从对话中提取用户偏好变化。
        返回可直接传给 update_preferences 的字段 dict，无发现则返回空 dict。

        简单规则匹配（不调用 LLM，保持低延迟）：
        - "我喜欢X" / "我偏好X" → liked_activities
        - "我不喜欢X" / "我讨厌X" → disliked_activities
        - "预算X元/块" → budget_level
        - "我喜欢吃X" / "X菜" → cuisine_preference
        """
        fields = {}

        # 预算提取
        budget_match = re.search(r'预算[大概约是]?(\d+)', user_msg)
        if budget_match:
            amount = int(budget_match.group(1))
            if amount < 1000:
                fields["budget_level"] = "经济型"
            elif amount < 3000:
                fields["budget_level"] = "舒适型"
            elif amount < 8000:
                fields["budget_level"] = "高端型"
            else:
                fields["budget_level"] = "奢华型"

        # 喜欢的活动
        like_match = re.findall(r'(?:我)?喜欢([一-鿿]{2,6})', user_msg)
        if like_match:
            fields["liked_activities"] = like_match

        # 不喜欢的活动
        dislike_match = re.findall(r'(?:我)?(?:不喜欢|讨厌|不想)([一-鿿]{2,6})', user_msg)
        if dislike_match:
            fields["disliked_activities"] = dislike_match

        # 美食偏好
        cuisine_match = re.findall(r'(?:喜欢吃|想吃|爱吃)([一-鿿]{2,6})', user_msg)
        if cuisine_match:
            fields["cuisine_preference"] = cuisine_match

        return fields

    async def save_summary(
        self,
        session_id: str,
        user_id: str,
        pg_session_id: uuid.UUID,
        summary: str,
        key_points: list[str],
    ) -> None:
        """保存对话摘要到长期记忆"""
        await models.save_summary(user_id, pg_session_id, summary, key_points)
