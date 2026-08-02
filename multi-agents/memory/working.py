"""
工作记忆 — 进程内中间推理状态存储

存储当前请求的中间推理状态，使用进程内存 dict 实现。
零延迟读写，按 session_id 隔离，请求结束后可选择性持久化到短期记忆。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional


class WorkingMemory:
    """
    工作记忆：维护当前对话的中间推理状态。
    - 进程内存储，零延迟读写
    - 按 session_id 隔离
    - 请求结束后可选择性持久化到短期记忆
    """

    def __init__(self, max_entries_per_session: int = 50):
        self.max_entries_per_session = max_entries_per_session
        # history entries per session: each entry has timestamp + data
        self._store: dict[str, list[dict]] = {}
        # merged context per session
        self._context: dict[str, dict] = {}
        self._lock = threading.Lock()

    def update(self, session_id: str, data: dict) -> None:
        """更新工作记忆（追加 entry + 合并到 context）"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        with self._lock:
            # Append to history
            if session_id not in self._store:
                self._store[session_id] = []
            self._store[session_id].append(entry)

            # Trim oldest entries if exceeding max
            if len(self._store[session_id]) > self.max_entries_per_session:
                excess = len(self._store[session_id]) - self.max_entries_per_session
                self._store[session_id] = self._store[session_id][excess:]

            # Merge into context
            if session_id not in self._context:
                self._context[session_id] = {}
            self._context[session_id].update(data)

    def get_context(self, session_id: str) -> dict:
        """获取当前 session 的完整上下文（所有 update 合并后的结果）"""
        with self._lock:
            return dict(self._context.get(session_id, {}))

    def get_history(self, session_id: str, last_n: int = 10) -> list[dict]:
        """获取最近 N 条工作记忆记录（带 timestamp）"""
        with self._lock:
            history = self._store.get(session_id, [])
            return list(history[-last_n:])

    def clear(self, session_id: str) -> None:
        """清除指定 session 的工作记忆"""
        with self._lock:
            self._store.pop(session_id, None)
            self._context.pop(session_id, None)

    def export_for_persistence(self, session_id: str) -> dict:
        """导出工作记忆，用于持久化到短期/长期记忆"""
        with self._lock:
            context = dict(self._context.get(session_id, {}))
            history = list(self._store.get(session_id, []))
        return {
            "session_id": session_id,
            "context": context,
            "history": history,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
