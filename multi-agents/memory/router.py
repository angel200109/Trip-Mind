"""
记忆路由器 — 根据用户请求意图决定从哪些层读取记忆

决策逻辑：
- 工作记忆：始终读取（零延迟，进程内）
- 短期记忆：session 已有历史时读取
- 长期记忆：
    - 偏好：总是加载（画像读取唯一入口，PG 单行成本低）
    - 知识库：query 含攻略/景点/推荐等信息检索词汇时加载
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .working import WorkingMemory
    from .short_term import ShortTermMemory
    from .long_term import LongTermMemory

# ── 意图关键词正则 ──────────────────────────────────────────────────────────
_KNOW_PATTERN = re.compile(r"攻略|景点|美食|推荐|交通|天气|路线|怎么去|哪里好玩|门票|住哪|吃什么")


class MemoryRouter:
    """
    记忆路由器：根据用户请求意图决定从哪些层读取记忆。
    - 工作记忆：始终读取
    - 短期记忆：session 已有历史时读取
    - 偏好：总是加载
    - 知识库：按关键词按需加载
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

        # 3. 长期记忆：偏好总是加载（PG 单行读取成本低，作为画像唯一入口）
        context["preferences"] = await self.long_term.get_preferences(user_id)

        # 知识库：按关键词按需加载
        if self._needs_knowledge(user_query):
            context["knowledge"] = await self.long_term.search_knowledge(user_query)

        return context

    # ── 意图判断 ──────────────────────────────────────────────────────────────

    def _needs_knowledge(self, query: str) -> bool:
        """判断是否需要检索知识库"""
        return bool(_KNOW_PATTERN.search(query))
