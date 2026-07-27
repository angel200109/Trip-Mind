"""会话管理服务 - 封装 chat_history_manager，适配前端格式"""
from datetime import datetime, timedelta
from typing import List, Optional
from chat_history_manager import get_chat_history_manager, ChatHistoryManager
from schemas.models import ConversationListItem, ConversationDetail, MessageItem


def compute_group_label(updated_at: str) -> str:
    """根据更新时间计算分组标签"""
    try:
        dt = datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return "更早"

    now = datetime.now()
    diff = now - dt

    if diff < timedelta(days=1) and dt.date() == now.date():
        return "今天"
    elif diff < timedelta(days=2) and dt.date() == (now - timedelta(days=1)).date():
        return "昨天"
    elif diff < timedelta(days=7):
        return "7天内"
    elif diff < timedelta(days=30):
        return "30天内"
    else:
        return "更早"


class ConversationService:
    """会话管理服务"""

    def __init__(self):
        self._manager: ChatHistoryManager = get_chat_history_manager()

    def list_conversations(self, limit: int = 50) -> List[ConversationListItem]:
        """获取会话列表（不含消息内容）"""
        sessions = self._manager.get_user_sessions(limit=limit)
        return [
            ConversationListItem(
                id=s.session_id,
                title=s.title,
                groupLabel=compute_group_label(s.updated_at),
                messageCount=s.message_count,
            )
            for s in sessions
        ]

    def get_conversation(self, session_id: str) -> Optional[ConversationDetail]:
        """获取会话详情（含所有消息）"""
        sessions = self._manager.get_user_sessions(limit=9999)
        session = next((s for s in sessions if s.session_id == session_id), None)
        if not session:
            return None

        messages = self._manager.get_session_messages(session_id)
        return ConversationDetail(
            id=session.session_id,
            title=session.title,
            groupLabel=compute_group_label(session.updated_at),
            messages=[
                MessageItem(
                    role="assistant" if m.message_type == "ai" else "user",
                    content=m.content,
                )
                for m in messages
            ],
        )

    def create_conversation(self, title: Optional[str] = None) -> ConversationListItem:
        """创建新会话"""
        session_id = self._manager.create_session(title=title or "新的对话")
        return ConversationListItem(
            id=session_id,
            title=title or "新的对话",
            groupLabel="今天",
            messageCount=0,
        )

    def delete_conversation(self, session_id: str) -> bool:
        """删除会话"""
        self._manager.delete_session(session_id)
        return True

    def update_title(self, session_id: str, title: str) -> bool:
        """更新会话标题"""
        self._manager.update_session_title(session_id, title)
        return True

    def add_message(self, session_id: str, role: str, content: str) -> int:
        """添加消息到会话"""
        message_type = "ai" if role == "assistant" else "user"
        return self._manager.add_message(session_id, message_type, content)


_conversation_service: Optional[ConversationService] = None


def get_conversation_service() -> ConversationService:
    """获取全局会话管理服务实例"""
    global _conversation_service
    if _conversation_service is None:
        _conversation_service = ConversationService()
    return _conversation_service
