"""SSE 流式会话管理 - 支持断线重连和 chunk 重放"""
import time
import threading
from typing import Dict, List, Optional, Literal
from dataclasses import dataclass, field


@dataclass
class StreamSession:
    """单个流式会话"""
    request_id: str
    conversation_id: str
    chunks: List[dict] = field(default_factory=list)
    status: Literal["streaming", "done", "error"] = "streaming"
    created_at: float = field(default_factory=time.time)
    TTL: int = 600  # 10 分钟过期


class StreamSessionManager:
    """管理所有活跃的流式会话"""

    def __init__(self):
        self._sessions: Dict[str, StreamSession] = {}
        self._lock = threading.Lock()

    def create(self, request_id: str, conversation_id: str) -> StreamSession:
        """创建新的流式会话"""
        with self._lock:
            session = StreamSession(
                request_id=request_id,
                conversation_id=conversation_id,
            )
            self._sessions[request_id] = session
            self.cleanup_expired()
            return session

    def get(self, request_id: str) -> Optional[StreamSession]:
        """获取会话"""
        return self._sessions.get(request_id)

    def append_chunk(self, request_id: str, chunk: dict):
        """追加 chunk 到会话"""
        session = self._sessions.get(request_id)
        if session:
            session.chunks.append(chunk)

    def replay_from(self, request_id: str, last_chunk_id: int) -> List[dict]:
        """从指定 chunkId 之后开始重放"""
        session = self._sessions.get(request_id)
        if not session:
            return []
        return [c for c in session.chunks if c.get("chunkId", 0) > last_chunk_id]

    def mark_done(self, request_id: str):
        """标记会话完成"""
        session = self._sessions.get(request_id)
        if session:
            session.status = "done"

    def mark_error(self, request_id: str):
        """标记会话出错"""
        session = self._sessions.get(request_id)
        if session:
            session.status = "error"

    def cleanup_expired(self):
        """清理过期会话"""
        now = time.time()
        with self._lock:
            expired = [
                rid for rid, s in self._sessions.items()
                if now - s.created_at > s.TTL
            ]
            for rid in expired:
                del self._sessions[rid]


# 全局单例
_stream_session_manager: Optional[StreamSessionManager] = None


def get_stream_session_manager() -> StreamSessionManager:
    """获取全局流式会话管理器"""
    global _stream_session_manager
    if _stream_session_manager is None:
        _stream_session_manager = StreamSessionManager()
    return _stream_session_manager
