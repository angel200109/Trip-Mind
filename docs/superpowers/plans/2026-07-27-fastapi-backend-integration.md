# FastAPI 后端集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 multi-agents 目录下构建 FastAPI 服务，对外暴露兼容 TripMate 前端的 SSE 流式对话接口，用 LangGraph astream_events 实现 Agent 进度 + Summarizer token 级流式输出。

**Architecture:** FastAPI 服务复用现有 LangGraph graph/agent_nodes/tools，新增 API 路由层 + 流式适配层 + 会话管理适配层。前端仅需修改 proxy 地址和少量 API 调用逻辑。

**Tech Stack:** Python 3.12, FastAPI, uvicorn, SSE (StreamingResponse), LangGraph astream_events, SQLite (现有), Vue 3 + Pinia (TripMate)

## Global Constraints

- Python >= 3.12
- FastAPI >= 0.100.0
- 复用现有 `chat_history_manager.py`、`graph/workflow.py`、`agent_nodes/`、`tools/`
- SSE 数据格式必须兼容前端现有 `fetchEventSourceRequest.ts` 解析器
- 不修改现有 LangGraph agent 核心逻辑（除 Summarizer 加 streaming=True）

---

### Task 1: FastAPI 基础骨架 + 依赖安装

**Files:**
- Create: `multi-agents/server.py`
- Create: `multi-agents/api/__init__.py`
- Create: `multi-agents/services/__init__.py`
- Create: `multi-agents/schemas/__init__.py`
- Create: `multi-agents/schemas/models.py`
- Modify: `multi-agents/requirements.txt`

**Interfaces:**
- Consumes: 无
- Produces: `server.py` 中的 FastAPI app 实例；`schemas/models.py` 中的 `ApiResponse`, `ChatRequest`, `ConversationResponse`, `SSEChunk` Pydantic 模型

- [ ] **Step 1: 安装依赖**

```bash
cd multi-agents
pip install fastapi uvicorn[standard] python-multipart
```

- [ ] **Step 2: 追加 requirements.txt**

在 `multi-agents/requirements.txt` 末尾追加：

```
# FastAPI 服务
fastapi>=0.100.0
uvicorn[standard]>=0.20.0
python-multipart>=0.0.6
```

- [ ] **Step 3: 创建 schemas/models.py**

```python
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
```

- [ ] **Step 4: 创建 schemas/__init__.py**

```python
from .models import (
    ApiResponse, MessageItem, ChatRequest, CreateConversationRequest,
    UpdateTitleRequest, ConversationListItem, ConversationDetail, SSEChunk
)
```

- [ ] **Step 5: 创建 api/__init__.py 和 services/__init__.py**

两个空的 `__init__.py` 文件。

- [ ] **Step 6: 创建 server.py**

```python
"""FastAPI 服务入口"""
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

app = FastAPI(title="Smart Travel Multi-Agents API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（图片上传）
images_dir = Path(__file__).parent / "images"
images_dir.mkdir(exist_ok=True)
app.mount("/static/images", StaticFiles(directory=str(images_dir)), name="images")

# 注册路由（后续 Task 创建后取消注释）
# from api.chat import router as chat_router
# from api.conversations import router as conversations_router
# from api.upload import router as upload_router
# app.include_router(chat_router)
# app.include_router(conversations_router)
# app.include_router(upload_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
```

- [ ] **Step 7: 验证启动**

```bash
cd multi-agents
python server.py
```

访问 `http://localhost:8000/health` 应返回 `{"status": "ok"}`。

- [ ] **Step 8: Commit**

```bash
git add server.py api/ services/ schemas/ requirements.txt
git commit -m "feat: FastAPI 基础骨架 + Pydantic 模型定义"
```

---

### Task 2: 会话管理服务 + API 路由

**Files:**
- Create: `multi-agents/services/conversation_service.py`
- Create: `multi-agents/api/conversations.py`
- Modify: `multi-agents/server.py` (注册路由)

**Interfaces:**
- Consumes: `chat_history_manager.py` 中的 `ChatHistoryManager`（`create_session`, `get_user_sessions`, `get_session_messages`, `delete_session`, `update_session_title`, `add_message`）；`schemas/models.py` 中的模型
- Produces: `conversation_service.py` 中的 `ConversationService` 类（`list_conversations()`, `get_conversation(id)`, `create_conversation(title)`, `delete_conversation(id)`, `update_title(id, title)`, `add_message(session_id, role, content)`）；`api/conversations.py` 中的 FastAPI router

- [ ] **Step 1: 创建 services/conversation_service.py**

```python
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
```

- [ ] **Step 2: 创建 api/conversations.py**

```python
"""会话管理 API 路由"""
from fastapi import APIRouter, HTTPException
from schemas.models import ApiResponse, CreateConversationRequest, UpdateTitleRequest
from services.conversation_service import get_conversation_service

router = APIRouter()


@router.get("/conversations")
async def list_conversations():
    service = get_conversation_service()
    conversations = service.list_conversations()
    return ApiResponse(data=[c.model_dump() for c in conversations])


@router.get("/conversations/{session_id}")
async def get_conversation(session_id: str):
    service = get_conversation_service()
    conversation = service.get_conversation(session_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return ApiResponse(data=conversation.model_dump())


@router.post("/conversations")
async def create_conversation(req: CreateConversationRequest):
    service = get_conversation_service()
    conversation = service.create_conversation(title=req.title)
    return ApiResponse(data=conversation.model_dump())


@router.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str):
    service = get_conversation_service()
    service.delete_conversation(session_id)
    return ApiResponse(data=True)


@router.patch("/conversations/{session_id}/title")
async def update_title(session_id: str, req: UpdateTitleRequest):
    service = get_conversation_service()
    service.update_title(session_id, req.title)
    return ApiResponse(data=True)
```

- [ ] **Step 3: 注册路由到 server.py**

在 `server.py` 中取消注释并添加：

```python
from api.conversations import router as conversations_router
app.include_router(conversations_router)
```

- [ ] **Step 4: 验证**

```bash
cd multi-agents
python server.py
```

用 curl 测试：
```bash
curl http://localhost:8000/conversations
# 期望: {"data":[],"code":200,"msg":"SUCCESS","error":null,"serviceCode":200}

curl -X POST http://localhost:8000/conversations -H "Content-Type: application/json" -d '{"title":"测试会话"}'
# 期望: {"data":{"id":"default_user_...","title":"测试会话","groupLabel":"今天","messageCount":0},...}
```

- [ ] **Step 5: Commit**

```bash
git add services/conversation_service.py api/conversations.py server.py
git commit -m "feat: 会话管理服务 + REST API 路由"
```

---

### Task 3: SSE 流式会话管理器（StreamSession）

**Files:**
- Create: `multi-agents/services/stream_session.py`

**Interfaces:**
- Consumes: `schemas/models.py` 中的 `SSEChunk`
- Produces: `StreamSessionManager` 类（`create(request_id, conversation_id)`, `get(request_id)`, `append_chunk(request_id, chunk)`, `replay_from(request_id, last_chunk_id)`, `mark_done(request_id)`, `mark_error(request_id)`, `cleanup_expired()`）

- [ ] **Step 1: 创建 services/stream_session.py**

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add services/stream_session.py
git commit -m "feat: SSE 流式会话管理器（支持断线重连重放）"
```

---

### Task 4: Summarizer 流式改造

**Files:**
- Modify: `multi-agents/agent_nodes/summarizer_agent.py` (第 60-65 行的 LLM 实例化加 `streaming=True`)

**Interfaces:**
- Consumes: 无新接口
- Produces: Summarizer 的 LLM 调用会生成 `on_chat_model_stream` 事件，可被 `astream_events()` 捕获

- [ ] **Step 1: 修改 summarizer_agent.py**

在第 60 行处，给 ChatOpenAI 实例添加 `streaming=True`：

```python
    llm = ChatOpenAI(
        model=QWEN3_MODEL,
        base_url=QWEN3_API_BASE,
        api_key=DASHSCOPE_API_KEY,
        temperature=QWEN3_TEMPERATURE,
        streaming=True
    )
```

仅增加 `streaming=True` 这一个参数。现有 `ainvoke()` 调用方式不变（LangGraph 的 `astream_events` 会拦截 streaming token）。

- [ ] **Step 2: 验证现有 Streamlit 不受影响**

```bash
cd multi-agents
streamlit run app.py
```

发送一条消息，确认 Streamlit 应用正常工作（`ainvoke` 加 `streaming=True` 后行为不变，只是 astream_events 可以捕获中间 token）。

- [ ] **Step 3: Commit**

```bash
git add agent_nodes/summarizer_agent.py
git commit -m "feat: Summarizer LLM 启用 streaming 模式"
```

---

### Task 5: 核心 - 流式对话服务（chat_service）

**Files:**
- Create: `multi-agents/services/chat_service.py`

**Interfaces:**
- Consumes: `graph/workflow.py` 中的 `travel_graph`；`graph/state.py` 中的 `GlobalState`；`services/conversation_service.py` 中的 `ConversationService`；`services/stream_session.py` 中的 `StreamSessionManager`；`schemas/models.py` 中的 `SSEChunk`, `ChatRequest`
- Produces: `stream_chat(request: ChatRequest)` async generator，yield SSE 格式字符串

- [ ] **Step 1: 创建 services/chat_service.py**

```python
"""流式对话服务 - 调用 multi-agents + 生成 SSE 事件流"""
import uuid
import json
import asyncio
from typing import AsyncGenerator, Optional, Dict, Any, List

from langchain_core.messages import HumanMessage, AIMessage
from graph.workflow import travel_graph
from graph.state import GlobalState
from schemas.models import ChatRequest, SSEChunk
from services.conversation_service import get_conversation_service
from services.stream_session import get_stream_session_manager


# Agent 节点进度文案
AGENT_STATUS_MAP = {
    "main_agent": "正在分析您的需求...",
    "planner_agent": "正在规划行程方案...",
    "executor_agent": "正在执行查询任务...",
    "summarizer_agent": "正在整理旅行方案...",
    "feedback_agent": "正在处理您的反馈...",
}

# 工具调用进度文案
TOOL_STATUS_MAP = {
    "train_query": "正在查询火车票信息...",
    "gaode_weather": "正在查询目的地天气...",
    "gaode_hotel_search": "正在搜索酒店...",
    "gaode_poi_search": "正在搜索景点...",
    "gaode_routing": "正在规划路线...",
    "rag_search": "正在查阅旅游攻略...",
    "flight_query": "正在查询航班...",
    "lucky_day": "正在查询黄历吉日...",
    "biying_search": "正在搜索相关信息...",
}


def format_sse(event: str, data: dict) -> str:
    """格式化 SSE 事件字符串"""
    chunk_id = data.get("chunkId", 0)
    data_str = json.dumps(data, ensure_ascii=False)
    return f"id: {chunk_id}\nevent: {event}\ndata: {data_str}\n\n"


def build_state_from_messages(messages: List[dict]) -> GlobalState:
    """从前端消息列表构建 GlobalState"""
    langchain_messages = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        # 跳过 progress 占位消息
        if role == "assistant" and (not content or msg.get("progress")):
            continue
        if role == "user":
            if isinstance(content, list):
                # 多模态消息，取文本部分
                text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
                langchain_messages.append(HumanMessage(content=text))
            else:
                langchain_messages.append(HumanMessage(content=content))
        elif role == "assistant" and content:
            langchain_messages.append(AIMessage(content=content))

    # 获取最后一条用户消息作为 user_query
    user_query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                user_query = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
            else:
                user_query = content
            break

    return {
        "user_query": user_query,
        "messages": langchain_messages,
        "planner_context": None,
        "executor_context": None,
        "summarizer_context": None,
        "current_agent": None,
        "next_agent": None,
        "is_complete": False,
    }


async def stream_chat(request: ChatRequest) -> AsyncGenerator[str, None]:
    """
    核心流式对话生成器
    
    1. 解析请求，构建 GlobalState
    2. 处理重连（如果带 requestId + lastChunkId）
    3. 用 astream_events 执行 graph，拦截事件生成 SSE chunk
    """
    session_mgr = get_stream_session_manager()
    conv_service = get_conversation_service()

    # 处理重连
    if request.requestId and request.lastChunkId and request.lastChunkId > 0:
        existing = session_mgr.get(request.requestId)
        if existing:
            # 重放缺失的 chunk
            missed = session_mgr.replay_from(request.requestId, request.lastChunkId)
            for chunk in missed:
                yield format_sse("message", chunk)
            if existing.status == "done":
                yield format_sse("done", {"requestId": request.requestId, "chunkId": len(existing.chunks) + 1, "done": True})
            return

    # 新请求
    request_id = request.requestId or str(uuid.uuid4())
    
    # 确保有 conversation
    conversation_id = request.conversationId
    if not conversation_id:
        conv = conv_service.create_conversation()
        conversation_id = conv.id

    # 创建 stream session
    session_mgr.create(request_id, conversation_id)

    chunk_id = 0

    # 发送 meta 事件
    chunk_id += 1
    meta_chunk = {
        "requestId": request_id,
        "chunkId": chunk_id,
        "type": "meta",
        "functionName": "",
        "data": "",
        "conversationId": conversation_id,
    }
    session_mgr.append_chunk(request_id, meta_chunk)
    yield format_sse("message", meta_chunk)

    # 保存用户消息到数据库
    user_query = ""
    for msg in reversed(request.chatMessages):
        if msg.role == "user":
            content = msg.content
            if isinstance(content, list):
                user_query = next((c.get("text", "") for c in content if c.get("type") == "text"), str(content))
            else:
                user_query = str(content)
            break

    conv_service.add_message(conversation_id, "user", user_query)

    # 构建 state 并执行 graph
    messages_dicts = [{"role": m.role, "content": m.content} for m in request.chatMessages]
    state = build_state_from_messages(messages_dicts)

    # 跟踪已发送的 status 事件（避免重复）
    sent_agents = set()
    sent_tools = set()
    final_content = ""

    try:
        async for event in travel_graph.astream_events(state, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")

            # Agent 节点开始
            if kind == "on_chain_start" and name in AGENT_STATUS_MAP:
                if name not in sent_agents:
                    sent_agents.add(name)
                    chunk_id += 1
                    status_chunk = {
                        "requestId": request_id,
                        "chunkId": chunk_id,
                        "type": "status",
                        "functionName": name,
                        "data": AGENT_STATUS_MAP[name],
                    }
                    session_mgr.append_chunk(request_id, status_chunk)
                    yield format_sse("message", status_chunk)

            # 工具调用开始
            elif kind == "on_tool_start" and name in TOOL_STATUS_MAP:
                if name not in sent_tools:
                    sent_tools.add(name)
                    chunk_id += 1
                    tool_chunk = {
                        "requestId": request_id,
                        "chunkId": chunk_id,
                        "type": "status",
                        "functionName": name,
                        "data": TOOL_STATUS_MAP[name],
                    }
                    session_mgr.append_chunk(request_id, tool_chunk)
                    yield format_sse("message", tool_chunk)

            # Summarizer LLM 流式 token
            elif kind == "on_chat_model_stream":
                # 只拦截 summarizer 节点的 token
                tags = event.get("tags", [])
                parent_ids = event.get("parent_ids", [])
                metadata = event.get("metadata", {})
                # 通过 langgraph_node 判断是否是 summarizer
                node_name = metadata.get("langgraph_node", "")
                if node_name == "summarizer_agent":
                    chunk_content = event.get("data", {}).get("chunk", None)
                    if chunk_content and hasattr(chunk_content, "content") and chunk_content.content:
                        token = chunk_content.content
                        final_content += token
                        chunk_id += 1
                        content_chunk = {
                            "requestId": request_id,
                            "chunkId": chunk_id,
                            "type": "content",
                            "functionName": "",
                            "data": token,
                        }
                        session_mgr.append_chunk(request_id, content_chunk)
                        yield format_sse("message", content_chunk)

    except Exception as e:
        chunk_id += 1
        error_chunk = {
            "requestId": request_id,
            "chunkId": chunk_id,
            "type": "error",
            "functionName": "",
            "data": f"服务处理出错: {str(e)}",
        }
        session_mgr.append_chunk(request_id, error_chunk)
        session_mgr.mark_error(request_id)
        yield format_sse("error", error_chunk)
        return

    # 如果没有从 streaming 拿到内容（fallback：从最终 state 提取）
    if not final_content:
        # 用同步方式获取结果
        try:
            result = await travel_graph.ainvoke(state)
            summarizer_ctx = result.get("summarizer_context") or {}
            answer = summarizer_ctx.get("final_summary", "")
            if not answer:
                planner_ctx = result.get("planner_context") or {}
                answer = planner_ctx.get("clarification_question", "处理完成")
            
            # 一次性发送完整内容
            chunk_id += 1
            content_chunk = {
                "requestId": request_id,
                "chunkId": chunk_id,
                "type": "content",
                "functionName": "",
                "data": answer,
            }
            session_mgr.append_chunk(request_id, content_chunk)
            yield format_sse("message", content_chunk)
            final_content = answer
        except Exception as e:
            chunk_id += 1
            error_chunk = {
                "requestId": request_id,
                "chunkId": chunk_id,
                "type": "error",
                "functionName": "",
                "data": f"服务处理出错: {str(e)}",
            }
            session_mgr.append_chunk(request_id, error_chunk)
            session_mgr.mark_error(request_id)
            yield format_sse("error", error_chunk)
            return

    # 保存 AI 回复到数据库
    if final_content:
        conv_service.add_message(conversation_id, "assistant", final_content)

    # 发送 done 事件
    chunk_id += 1
    done_chunk = {"requestId": request_id, "chunkId": chunk_id, "done": True}
    session_mgr.append_chunk(request_id, done_chunk)
    session_mgr.mark_done(request_id)
    yield format_sse("done", done_chunk)
```

- [ ] **Step 2: Commit**

```bash
git add services/chat_service.py
git commit -m "feat: 核心流式对话服务（astream_events + SSE 事件生成）"
```

---

### Task 6: 流式对话 API 路由 + 文件上传

**Files:**
- Create: `multi-agents/api/chat.py`
- Create: `multi-agents/api/upload.py`
- Modify: `multi-agents/server.py` (注册所有路由)

**Interfaces:**
- Consumes: `services/chat_service.py` 中的 `stream_chat(request)`；`schemas/models.py` 中的 `ChatRequest`, `ApiResponse`
- Produces: `POST /chatMessage/stream` SSE 端点；`POST /uploadFile` 文件上传端点

- [ ] **Step 1: 创建 api/chat.py**

```python
"""流式对话 API 路由"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from schemas.models import ChatRequest
from services.chat_service import stream_chat

router = APIRouter()


@router.post("/chatMessage/stream")
async def chat_stream(request: ChatRequest):
    """SSE 流式对话端点"""
    return StreamingResponse(
        stream_chat(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 2: 创建 api/upload.py**

```python
"""文件上传 API 路由"""
import time
import os
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from schemas.models import ApiResponse

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "images"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/jpg"}


@router.post("/uploadFile")
async def upload_file(file: UploadFile = File(...)):
    """上传图片文件"""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=442, detail="图片格式错误，仅支持 png/jpeg/webp")

    ext = file.filename.split(".")[-1] if file.filename else "png"
    filename = f"{int(time.time() * 1000)}.{ext}"
    filepath = UPLOAD_DIR / filename

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    host = os.getenv("UPLOAD_HOST", "localhost:8000")
    url = f"{host}/static/images/{filename}"
    return ApiResponse(data=url)
```

- [ ] **Step 3: 注册所有路由到 server.py**

修改 `server.py`，将路由注册代码改为：

```python
from api.chat import router as chat_router
from api.conversations import router as conversations_router
from api.upload import router as upload_router

app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(upload_router)
```

- [ ] **Step 4: 验证 SSE 端点**

```bash
cd multi-agents
python server.py
```

用 curl 测试 SSE：
```bash
curl -N -X POST http://localhost:8000/chatMessage/stream \
  -H "Content-Type: application/json" \
  -d '{"chatMessages":[{"role":"user","content":"杭州今天天气怎么样"}]}'
```

期望看到 SSE 事件流：`id: 1\nevent: message\ndata: {...}\n\n` 格式。

- [ ] **Step 5: Commit**

```bash
git add api/chat.py api/upload.py server.py
git commit -m "feat: 流式对话 SSE 端点 + 文件上传端点"
```

---

### Task 7: 前端适配

**Files:**
- Modify: `TripMate/vite.config.ts` (proxy target)
- Modify: `TripMate/src/api/conversation.ts` (简化 updateConversation)
- Modify: `TripMate/src/api/fetchEventSourceRequest.ts` (加 conversationId)
- Modify: `TripMate/src/store/index.ts` (去掉 syncCurrentConversation 中的消息同步，从 meta 事件获取 conversationId)

**Interfaces:**
- Consumes: FastAPI 后端的 `/conversations`、`/chatMessage/stream` 接口
- Produces: 前端能正常与 FastAPI 后端通信

- [ ] **Step 1: 修改 vite.config.ts**

将 proxy target 从 `http://127.0.0.1:3001` 改为 `http://127.0.0.1:8000`：

```typescript
proxy: {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
    rewrite: (path) => path.replace(/^\/api/, ""),
  },
},
```

- [ ] **Step 2: 修改 src/api/conversation.ts**

替换 `updateConversationApi` 为仅更新标题：

```typescript
/**
 * 更新对话标题
 * PATCH /conversations/:id/title
 */
export const updateConversationTitleApi = async (
  id: string,
  title: string,
): Promise<ApiResponse<boolean>> => {
  const response = await axiosInstance.patch<ApiResponse<boolean>>(
    `/conversations/${id}/title`,
    { title },
  );
  return response.data;
};
```

保留 `getConversationsApi`、`getConversationDetailApi`、`createConversationApi`、`deleteConversationApi` 不变。

- [ ] **Step 3: 修改 src/api/fetchEventSourceRequest.ts**

在发送请求体中加入 `conversationId`：

找到构建请求 body 的位置，在 `chatMessages` 旁边加入 `conversationId`：

```typescript
body: JSON.stringify({
  chatMessages: params.chatMessages,
  conversationId: store.currentConversationId || undefined,
  requestId: streamState.requestId,
  lastChunkId: streamState.lastChunkId,
}),
```

在 `meta` 事件处理中，解析 `conversationId`：

```typescript
if (parsedData.type === "meta") {
  if (parsedData.conversationId) {
    store.currentConversationId = parsedData.conversationId;
    // 如果是新创建的会话，刷新会话列表
    store.loadConversations();
  }
  // ... 其他 meta 处理
}
```

- [ ] **Step 4: 修改 src/store/index.ts**

1. 将 `syncCurrentConversation()` 简化为空操作（后端自动持久化，无需前端同步）：

```typescript
syncCurrentConversation() {
  // 后端自动持久化消息，前端不再需要同步
},
```

2. 在 `createConversation` 中，简化请求体（后端不再需要 groupLabel 和 messages）：

```typescript
async createConversation(content?: SendMessage) {
  const response = await createConversationApi({
    title: content ? getConversationTitle(content) : "新的对话",
    groupLabel: "今天",
    messages: [],
  });
  // ... 其余逻辑不变
}
```

注意：`createConversationApi` 现在后端只需要 title，但为兼容不改前端类型定义，后端会忽略多余字段。

- [ ] **Step 5: 验证端到端**

```bash
# 启动后端
cd multi-agents && python server.py

# 启动前端
cd TripMate && npm run dev
```

在浏览器中：
1. 打开 http://localhost:8080
2. 发送一条消息（如"杭州天气怎么样"）
3. 确认看到进度状态（"正在分析您的需求..."等）
4. 确认最终答案流式输出
5. 刷新页面，确认历史会话正确加载

- [ ] **Step 6: Commit**

```bash
cd TripMate
git add vite.config.ts src/api/conversation.ts src/api/fetchEventSourceRequest.ts src/store/index.ts
git commit -m "feat: 前端适配 FastAPI 后端（proxy + conversationId + 去掉消息同步）"
```

---

### Task 8: 集成测试与收尾

**Files:**
- Modify: `multi-agents/server.py` (如有小修)
- 无新文件

**Interfaces:**
- Consumes: 所有前序 Task 的产出
- Produces: 可用的完整系统

- [ ] **Step 1: 启动后端并检查日志**

```bash
cd multi-agents
python server.py
```

确认无启动报错，日志显示 MCP 工具加载成功。

- [ ] **Step 2: 全流程测试**

测试场景清单：
1. **新对话 + 简单查询**：发送 "杭州天气怎么样"，确认 status 事件 + content 流式
2. **新对话 + 复杂规划**：发送 "我想12月去杭州玩3天，预算5000"，确认完整 agent 流程
3. **会话切换**：创建多个对话，切换后消息正确加载
4. **刷新恢复**：刷新页面，确认历史会话完整
5. **重连机制**：在流式过程中中断网络（DevTools Network offline），恢复后确认续传

- [ ] **Step 3: 修复问题（如有）**

根据测试结果修复发现的问题。

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "feat: 集成测试通过，FastAPI + TripMate 前端对接完成"
```
