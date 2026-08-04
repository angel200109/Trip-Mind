"""会话管理服务 - 基于 PostgreSQL (db.models)，适配前端格式"""
from typing import List, Optional
import uuid

from db import models as db_models
from schemas.models import ConversationListItem, ConversationDetail, MessageItem

# 当前单用户模式
USER_ID = "default_user"

# 会话列表不再按时间分组，统一归为一组
GROUP_LABEL = "全部对话"


def _parse_session_id(session_id: str) -> Optional[uuid.UUID]:
    """前端传入的会话 ID 字符串转 UUID，非法返回 None"""
    try:
        return uuid.UUID(str(session_id))
    except (ValueError, TypeError):
        return None


class ConversationService:
    """会话管理服务（PostgreSQL）"""

    async def list_conversations(self, limit: int = 50) -> List[ConversationListItem]:
        """获取会话列表（不含消息内容）"""
        sessions = await db_models.get_user_sessions(USER_ID, limit=limit)
        return [
            ConversationListItem(
                id=str(s["id"]),
                title=s["title"] or "",
                groupLabel=GROUP_LABEL,
                messageCount=s.get("message_count", 0),
            )
            for s in sessions
        ]

    async def get_conversation(self, session_id: str) -> Optional[ConversationDetail]:
        """获取会话详情（含所有消息）"""
        sid = _parse_session_id(session_id)
        if not sid:
            return None

        sessions = await db_models.get_user_sessions(USER_ID, limit=9999)
        session = next((s for s in sessions if s["id"] == sid), None)
        if not session:
            return None

        messages = await db_models.get_session_messages(sid)
        return ConversationDetail(
            id=str(session["id"]),
            title=session["title"] or "",
            groupLabel=GROUP_LABEL,
            messages=[
                MessageItem(
                    role=m["role"],  # PG 中直接存 "user"/"assistant"
                    content=m["content"],
                )
                for m in messages
            ],
        )

    async def create_conversation(self, title: Optional[str] = None) -> ConversationListItem:
        """创建新会话"""
        session_id = await db_models.create_session(USER_ID, title or "新的对话")
        return ConversationListItem(
            id=str(session_id),
            title=title or "新的对话",
            groupLabel=GROUP_LABEL,
            messageCount=0,
        )

    async def delete_conversation(self, session_id: str) -> bool:
        """删除会话"""
        sid = _parse_session_id(session_id)
        if not sid:
            return False
        await db_models.delete_session(sid)
        return True

    async def update_title(self, session_id: str, title: str) -> bool:
        """更新会话标题"""
        sid = _parse_session_id(session_id)
        if not sid:
            return False
        await db_models.update_session_title(sid, title)
        return True

    async def add_message(self, session_id: str, role: str, content: str) -> int:
        """添加消息到会话"""
        sid = _parse_session_id(session_id)
        if not sid:
            return 0
        return await db_models.save_message(sid, role, content)


_conversation_service: Optional[ConversationService] = None


def get_conversation_service() -> ConversationService:
    """获取全局会话管理服务实例"""
    global _conversation_service
    if _conversation_service is None:
        _conversation_service = ConversationService()
    return _conversation_service
