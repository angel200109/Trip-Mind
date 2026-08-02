"""
记忆系统全局管理器
提供单例实例，供 workflow 各节点使用
"""
from __future__ import annotations

from typing import Optional

from .working import WorkingMemory
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .router import MemoryRouter
from .promotion import MemoryPromotion


class MemoryManager:
    """记忆系统全局管理器 — 持有所有层的实例"""

    def __init__(self):
        self.working = WorkingMemory()
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory()
        self.router = MemoryRouter(
            working_memory=self.working,
            short_term=self.short_term,
            long_term=self.long_term,
        )
        self.promotion = MemoryPromotion(
            short_term=self.short_term,
            long_term=self.long_term,
            working=self.working,
        )


_instance: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """获取全局记忆管理器单例"""
    global _instance
    if _instance is None:
        _instance = MemoryManager()
    return _instance
