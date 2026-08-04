"""
记忆提升 — 请求结束后决定哪些信息写入短期/长期记忆。

写回 pipeline:
1. 用户消息 + 助手回复 → Redis 短期记忆（热缓存）
2. 检测偏好变化（正则快路径 → LLM 兜底）→ 更新 PG user_preferences
3. 检测旅行计划完成 → 写入 PG travel_history
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
    3. 检测旅行计划完成 → 写入 PG travel_history
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
                "saved_to_pg": bool,
                "saved_to_short_term": bool,
                "preferences_updated": bool,
                "travel_history_saved": bool,
            }
        """
        result = {
            "saved_to_short_term": False,
            "preferences_updated": False,
            "travel_history_saved": False,
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

        # 3. 检测旅行计划
        travel_info = self._extract_travel_info(user_message, assistant_response)
        if travel_info and pg_session_id:
            await models.save_travel_history(
                user_id=user_id,
                session_id=pg_session_id,
                **travel_info,
            )
            result["travel_history_saved"] = True

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

    def _extract_travel_info(self, user_msg: str, assistant_msg: str) -> dict:
        """
        从对话中提取旅行计划信息。
        只在助手回复中明确包含行程规划内容时触发。
        返回可传给 save_travel_history 的 dict，无发现返回空 dict。
        """
        info = {}

        # 只在助手回复包含行程规划标志时触发
        plan_indicators = ["行程安排", "日程安排", "行程规划", "Day ", "第一天", "第1天"]
        if not any(indicator in assistant_msg for indicator in plan_indicators):
            return {}

        # 目的地提取
        dest_match = re.search(r'(?:去|到|游|玩)([一-鿿]{2,5})', user_msg)
        if dest_match:
            info["destination"] = dest_match.group(1)

        # 天数提取
        days_match = re.search(r'(\d+)\s*[天日]', user_msg)
        if days_match:
            info["travel_days"] = int(days_match.group(1))

        # 预算提取
        budget_match = re.search(r'预算[大概约是]?(\d+)', user_msg)
        if budget_match:
            info["budget"] = float(budget_match.group(1))

        # 出发地提取
        origin_match = re.search(r'从([一-鿿]{2,5})(?:出发|去|到)', user_msg)
        if origin_match:
            info["origin"] = origin_match.group(1)

        if info.get("destination"):
            info["status"] = "planned"
            return info

        return {}

    async def save_summary(
        self,
        session_id: str,
        user_id: str,
        pg_session_id: uuid.UUID,
        summary: str,
        key_points: list[str],
    ) -> None:
        """保存对话摘要到长期记忆（由 ContextCompressor 调用后执行）"""
        await models.save_summary(user_id, pg_session_id, summary, key_points)
