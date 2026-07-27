"""请求/响应 Pydantic 模型"""
from typing import Any, List, Optional, Literal
from pydantic import BaseModel


class ApiResponse(BaseModel):
    """统一 API 响应格式（兼容 AIGC-NODE）"""
    data: Any = None
    code: int = 200
    msg: str = "SUCCESS"
    error: Any = None
    serviceCode: int = 200


class MessageItem(BaseModel):
    """单条消息"""
    role: Literal["user", "assistant"]
    content: Any  # str 或 List[TextContent | ImageContent]


class ChatRequest(BaseModel):
    """流式对话请求"""
    chatMessages: List[MessageItem]
    conversationId: Optional[str] = None
    requestId: Optional[str] = None
    lastChunkId: Optional[int] = 0


class CreateConversationRequest(BaseModel):
    """创建会话请求"""
    title: Optional[str] = None


class UpdateTitleRequest(BaseModel):
    """更新会话标题请求"""
    title: str


class ConversationListItem(BaseModel):
    """会话列表项"""
    id: str
    title: str
    groupLabel: str
    messageCount: int


class ConversationDetail(BaseModel):
    """会话详情（含消息）"""
    id: str
    title: str
    groupLabel: str
    messages: List[MessageItem]


class SSEChunk(BaseModel):
    """SSE 数据体"""
    requestId: str
    chunkId: int
    type: Literal["meta", "status", "content", "function", "error"]
    functionName: str = ""
    data: Any = ""
    conversationId: Optional[str] = None
    done: Optional[bool] = None
