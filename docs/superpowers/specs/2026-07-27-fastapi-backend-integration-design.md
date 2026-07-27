# FastAPI 后端集成设计：multi-agents + TripMate 前端

## 概述

将 `multi-agents` 的 LangGraph 多智能体系统通过 FastAPI 服务对外暴露，适配 `TripMate` Vue 前端。前端保留所有性能优化（SSE 解析、分帧消费、VNode Markdown、虚拟滚动），后端全权负责消息持久化。

## 目标

- TripMate 前端几乎无需改动即可对接新后端
- 保留前端 5 项核心优化
- SSE 流式输出：Agent 进度事件 + Summarizer token 级流式
- 后端全权管理会话持久化，前端不再 PATCH 同步消息

---

## 项目结构

在 `multi-agents/` 下新增 FastAPI 服务，与现有代码共存：

```
multi-agents/
├── app.py                       # 现有 Streamlit（保留）
├── server.py                    # 【新建】FastAPI 入口
├── api/                         # 【新建】API 路由层
│   ├── __init__.py
│   ├── chat.py                  # /chatMessage/stream 流式对话
│   ├── conversations.py         # /conversations CRUD
│   └── upload.py                # /uploadFile 文件上传
├── services/                    # 【新建】业务逻辑层
│   ├── __init__.py
│   ├── chat_service.py          # multi-agents 调用 + 流式事件生成
│   ├── conversation_service.py  # 会话管理（封装 chat_history_manager）
│   └── stream_session.py        # SSE 会话管理（重连/重放）
├── schemas/                     # 【新建】请求/响应模型
│   ├── __init__.py
│   └── models.py                # Pydantic 模型
├── graph/                       # 现有（复用）
├── agent_nodes/                 # 现有（复用）
├── tools/                       # 现有（复用）
├── config/                      # 现有（复用）
├── chat_history_manager.py      # 现有（复用）
└── requirements.txt             # 追加 fastapi, uvicorn, python-multipart
```

---

## API 接口设计

### 统一响应格式

```json
{
  "data": <any>,
  "code": 200,
  "msg": "SUCCESS",
  "error": null,
  "serviceCode": 200
}
```

### 会话管理

| 接口 | 方法 | 请求体 | 说明 |
|------|------|--------|------|
| `/conversations` | GET | - | 获取会话列表（含 groupLabel，不含 messages） |
| `/conversations/:id` | GET | - | 获取完整会话（含 messages） |
| `/conversations` | POST | `{ title?: string }` | 创建空会话 |
| `/conversations/:id` | DELETE | - | 删除会话 |
| `/conversations/:id/title` | PATCH | `{ title: string }` | 重命名会话 |

**Conversation 返回结构（列表）：**
```json
{
  "id": "session_id",
  "title": "帮我规划杭州三日游",
  "groupLabel": "今天",
  "messageCount": 12
}
```

**Conversation 返回结构（详情）：**
```json
{
  "id": "session_id",
  "title": "帮我规划杭州三日游",
  "groupLabel": "今天",
  "messages": [
    { "role": "user", "content": "我想去杭州玩" },
    { "role": "assistant", "content": "好的，..." }
  ]
}
```

**groupLabel 计算规则：**
- 当天 → "今天"
- 前一天 → "昨天"
- 2-7 天前 → "7天内"
- 8-30 天前 → "30天内"
- >30 天 → "更早"

### 流式对话

```
POST /chatMessage/stream
Content-Type: application/json

{
  "chatMessages": [
    { "role": "user", "content": "我想去杭州玩3天" },
    { "role": "assistant", "content": "..." }
  ],
  "conversationId": "xxx",     // 可选，不传则自动创建
  "requestId": "uuid",         // 可选，重连时带上
  "lastChunkId": 0             // 可选，重连时从此处续传
}

Response: text/event-stream
```

### 文件上传

```
POST /uploadFile
Content-Type: multipart/form-data
Field: file (image/png, image/jpeg, image/webp)

Response: { "data": "localhost:8000/static/images/1234567890.png", ... }
```

---

## SSE 流式协议设计

### 事件格式

完全兼容前端现有 SSE 解析器（fetchEventSourceRequest.ts）：

```
id: <chunkId>
event: message | done | error
data: <JSON>
```

### 数据体结构（ServerDataType）

```json
{
  "requestId": "uuid",
  "chunkId": 1,
  "type": "meta | status | content | function | error",
  "functionName": "",
  "data": "..."
}
```

### 事件序列示例

```
id: 1
event: message
data: {"requestId":"uuid","chunkId":1,"type":"meta","functionName":"","data":"","conversationId":"xxx"}

id: 2
event: message
data: {"requestId":"uuid","chunkId":2,"type":"status","functionName":"planner_agent","data":"正在分析您的旅行需求..."}

id: 3
event: message
data: {"requestId":"uuid","chunkId":3,"type":"status","functionName":"executor_agent","data":"正在查询天气信息..."}

id: 4
event: message
data: {"requestId":"uuid","chunkId":4,"type":"status","functionName":"gaode_weather","data":"正在查询目的地天气..."}

id: 5
event: message
data: {"requestId":"uuid","chunkId":5,"type":"status","functionName":"summarizer_agent","data":"正在整理旅行方案..."}

id: 6
event: message
data: {"requestId":"uuid","chunkId":6,"type":"content","functionName":"","data":"根据您的"}

id: 7
event: message
data: {"requestId":"uuid","chunkId":7,"type":"content","functionName":"","data":"需求，我为您"}

... (token 级流式)

id: N
event: done
data: {"requestId":"uuid","chunkId":N,"done":true}
```

### LangGraph 事件映射

| LangGraph 事件 | SSE type | 触发时机 |
|---|---|---|
| `on_chain_start`（node 级） | `status` | Agent 节点开始执行 |
| `on_tool_start` | `status` + functionName | MCP 工具调用开始 |
| `on_tool_end` | `function` + functionName | 工具返回结果（可选） |
| `on_llm_stream`（Summarizer） | `content` | 最终答案 token 流式 |
| 执行完成 | `done` event | 整个流程结束 |

### 进度文案

```python
AGENT_STATUS_MAP = {
    "main_agent": "正在分析您的需求...",
    "planner_agent": "正在规划行程方案...",
    "executor_agent": "正在执行查询任务...",
    "summarizer_agent": "正在整理旅行方案...",
    "feedback_agent": "正在处理您的反馈...",
}

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
```

---

## 流式实现方案

### Summarizer 流式改造

当前 Summarizer 使用 `llm.ainvoke()` 同步返回。改造为：

```python
# 在 summarizer_agent.py 中
# 改为使用 streaming=True 的 LLM 实例
llm_streaming = ChatOpenAI(
    model=QWEN3_MODEL,
    base_url=QWEN3_API_BASE,
    api_key=DASHSCOPE_API_KEY,
    temperature=0.7,
    streaming=True
)
```

在 `chat_service.py` 中通过 `astream_events()` 捕获：

```python
async def stream_multi_agents(state, request_id):
    chunk_id = 0

    async for event in travel_graph.astream_events(state, version="v2"):
        kind = event["event"]
        
        if kind == "on_chain_start" and event.get("name") in AGENT_STATUS_MAP:
            chunk_id += 1
            yield sse_chunk(request_id, chunk_id, "status", 
                          AGENT_STATUS_MAP[event["name"]], event["name"])

        elif kind == "on_tool_start":
            tool_name = event.get("name", "")
            if tool_name in TOOL_STATUS_MAP:
                chunk_id += 1
                yield sse_chunk(request_id, chunk_id, "status",
                              TOOL_STATUS_MAP[tool_name], tool_name)

        elif kind == "on_chat_model_stream":
            # 仅拦截 Summarizer 节点的 LLM 输出
            if is_summarizer_node(event):
                token = event["data"]["chunk"].content
                if token:
                    chunk_id += 1
                    yield sse_chunk(request_id, chunk_id, "content", token, "")

    chunk_id += 1
    yield sse_done(request_id, chunk_id)
```

### 重连机制（StreamSession）

```python
class StreamSession:
    request_id: str
    conversation_id: str
    chunks: List[dict]              # 缓存已发送的所有 chunk
    status: Literal["streaming", "done", "error"]
    created_at: float
    TTL = 600                       # 10 分钟过期

class StreamSessionManager:
    sessions: Dict[str, StreamSession]

    def create(self, request_id, conversation_id) -> StreamSession
    def get(self, request_id) -> Optional[StreamSession]
    def append_chunk(self, request_id, chunk: dict)
    def replay_from(self, request_id, last_chunk_id) -> List[dict]
    def mark_done(self, request_id)
    def cleanup_expired(self)       # 定期清理过期 session
```

---

## 前端改动（最小化）

| 文件 | 改动内容 |
|------|---------|
| `vite.config.ts` | proxy target 从 `http://127.0.0.1:3001` 改为 `http://127.0.0.1:8000` |
| `src/api/conversation.ts` | 去掉 updateConversation 中的 messages 字段；新增 `updateTitle` 方法 |
| `src/api/fetchEventSourceRequest.ts` | 请求体加 `conversationId` 字段；meta 事件中解析 `conversationId` |
| `src/store/index.ts` | 去掉 `syncCurrentConversation()` 中的消息同步逻辑；从 meta 事件获取 `conversationId` |

**不需要改动的（完整保留）：**
- SSE 解析层（粘包/半包处理、重连/续传机制）
- 分帧消费（buffer + requestAnimationFrame 批量刷新）
- Markdown VNode 渲染（markdown-it → Vue VNode diff）
- 代码高亮（Highlight.js）+ 数学公式（KaTeX）+ XSS 防护（DOMPurify）
- 动态高度虚拟滚动（vue-virtual-scroller）
- typewriter 动画

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| MCP 工具服务不可用 | Agent 跳过，Summarizer 注明"暂无该信息" |
| LLM API 超时/限流 | SSE error 事件：`{type:"error", data:"服务暂时繁忙"}` |
| 流式中途前端断开 | 后端继续执行，chunk 缓存等待重连 |
| conversationId 无效 | 返回 404，前端创建新会话 |
| StreamSession 过期（>10min） | error 事件，前端重新发起请求 |
| 并发控制 | 每个 conversation 同时只允许一个流式请求 |

---

## 依赖新增

`requirements.txt` 追加：

```
fastapi>=0.100.0
uvicorn[standard]>=0.20.0
python-multipart>=0.0.6
```

---

## 环境变量

```env
# 现有
DASHSCOPE_API_KEY=xxx
DEEPSEEK_API_KEY=xxx

# 新增
SERVER_PORT=8000
UPLOAD_DIR=./images
```

---

## 启动方式

```bash
# 后端
cd multi-agents
uvicorn server:app --host 0.0.0.0 --port 8000

# 前端
cd TripMate
npm run dev
# vite proxy: /api → http://127.0.0.1:8000
```

---

## 验证方式

1. 启动 FastAPI 后端，确认 `/conversations` 返回正确格式
2. 启动 TripMate 前端，发送一条消息
3. 确认 SSE 事件序列：meta → status（各 agent）→ content（流式 token）→ done
4. 确认前端 typewriter 动画正常、Markdown 渲染正常
5. 刷新页面，确认历史会话从后端正确加载
6. 测试断线重连：中断网络后恢复，确认 chunk 续传正常
