"""
记忆路由器 — 根据用户请求意图决定从哪些层读取记忆

决策逻辑：
- 工作记忆：始终读取（零延迟，进程内）
- 短期记忆：session 已有历史时读取
- 长期记忆：按需检索
    - 偏好：query 含偏好/风格/预算等关键词
    - 旅行历史：query 含 "上次"/"去过" 等回顾性词汇
    - 知识库：query 含攻略/景点/推荐等信息检索词汇
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .working import WorkingMemory
    from .short_term import ShortTermMemory
    from .long_term import LongTermMemory

# ── 意图关键词正则 ────────────────────────────────────────────────────────────
_PREF_PATTERN = re.compile(r"喜欢|偏好|风格|预算|习惯|口味|酒店|交通|不喜欢|讨厌")
_HIST_PATTERN = re.compile(r"上次|之前|去过|历史|以前|曾经|类似|同样|像上回")
_KNOW_PATTERN = re.compile(r"攻略|景点|美食|推荐|交通|天气|路线|怎么去|哪里好玩|门票|住哪|吃什么")


class MemoryRouter:
    """
    记忆路由器：根据用户请求意图决定从哪些层读取记忆。
    - 工作记忆：始终读取
    - 短期记忆：session 已有历史时读取
    - 长期记忆：按需检索（偏好/历史/知识库）
    """

    def __init__(
        self,
        working_memory: "WorkingMemory",
        short_term: "ShortTermMemory",
        long_term: "LongTermMemory",
    ):
        self.working_memory = working_memory
        self.short_term = short_term
        self.long_term = long_term

    async def load_context(
        self, session_id: str, user_id: str, user_query: str
    ) -> dict:
        """根据请求内容，决定从哪些层加载记忆，返回聚合后的上下文 dict。"""
        context: dict = {}

        # 1. 工作记忆：总是加载
        context["working"] = self.working_memory.get_context(session_id)

        # 2. 短期记忆：session 有历史时加载
        short_history = await self.short_term.get_history(session_id)
        if short_history:
            context["short_term"] = short_history

        # 3. 长期记忆：按需加载
        needs_pref = self._needs_preferences(user_query)
        needs_hist = self._needs_history(user_query)
        needs_know = self._needs_knowledge(user_query)

        # 兜底：长查询且没有命中任何关键词，默认加载偏好（成本低）
        if len(user_query) > 5 and not (needs_pref or needs_hist or needs_know):
            needs_pref = True

        if needs_pref:
            context["preferences"] = await self.long_term.get_preferences(user_id)
        if needs_hist:
            context["travel_history"] = await self.long_term.get_travel_history(
                user_id, limit=5
            )
        if needs_know:
            context["knowledge"] = await self.long_term.search_knowledge(user_query)

        return context

    # ── 意图判断 ──────────────────────────────────────────────────────────────

    def _needs_preferences(self, query: str) -> bool:
        """判断是否需要加载用户偏好"""
        return bool(_PREF_PATTERN.search(query))

    def _needs_history(self, query: str) -> bool:
        """判断是否需要加载旅行历史"""
        return bool(_HIST_PATTERN.search(query))

    def _needs_knowledge(self, query: str) -> bool:
        """判断是否需要检索知识库"""
        return bool(_KNOW_PATTERN.search(query))
