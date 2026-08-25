# Trip Mind

Trip Mind 是一款覆盖日常出行与旅游规划场景的智能助手 🧭。用户可以通过自然语言查询路线、天气、地点和交通信息，也可以描述目的地、出发地、日期、预算和兴趣偏好，由系统结合实时工具、景点知识库和多 Agent 协作完成出行决策与行程规划。

## 🎯 产品定位

Trip Mind 不是单一的聊天机器人，而是一个覆盖日常出行和旅游规划的智能出行工作台：

- **🚶 日常出行问答**：查询天气、路线、地点、交通和周边 POI 等即时信息。
- **🏞️ 景点攻略问答**：查询景点地址、特色、开放信息和游玩攻略。
- **📍 实时出行信息**：通过 MCP 工具查询天气、交通、路线、酒店、景点 POI 等动态信息。
- **🗺️ 旅游行程规划**：根据出发地、目的地、天数、预算和偏好拆解旅行任务，生成可执行方案。
- **👤 用户画像**：记录用户的旅行偏好，并在后续对话和行程规划中提供个性化参考。
- **💬 连续对话**：保存会话历史，结合短期记忆、长期偏好和工作记忆，让多轮对话保持上下文。
- **⚡ 流式交互**：后端通过 SSE 推送 Agent 节点、工具调用和回答内容，前端实时展示处理进度与最终答案。

系统的核心边界是：实时变化的出行信息交给外部工具查询，稳定的景点攻略交给 RAG 检索，多个查询结果由 Agent 汇总后再生成回答。

## 项目架构

```text
┌──────────────────────────────────────────────────────────────┐
│                         TripMate 前端                        │
│ Vue 3 + TypeScript + Vite + Pinia + Element Plus             │
│                                                              │
│ 对话页面 / 会话列表 / 消息渲染 / 流式输出 / 断线续传 / 上传     │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP + SSE
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                    multi-agents FastAPI 后端                  │
│                                                              │
│ API 层 → ChatService → LangGraph 工作流 → Agent / Tool / Memory│
└───────────────┬──────────────────┬───────────────────────────┘
                │                  │
                ▼                  ▼
       PostgreSQL 会话数据       外部 MCP 服务
                                  │
                                  ├─ 天气 / POI / 路线 / 酒店
                                  ├─ 火车票 / 航班
                                  └─ 搜索 / 其他旅行工具

                ┌─────────────────────────────────────────────┐
                │ RAG 知识库                                   │
                │ citydata CSV → Chunk → Embedding → ChromaDB   │
                │                         └→ BM25 + Rerank       │
                └─────────────────────────────────────────────┘
```

### 目录结构

```text
trip-mind/
├── TripMate/                 # Vue 前端
│   ├── src/api/              # HTTP、SSE、会话、上传接口
│   ├── src/views/Home/       # 主对话页面及其组件
│   ├── src/store/            # Pinia 对话状态
│   ├── src/components/       # Markdown 等通用组件
│   ├── src/router/           # 前端路由
│   └── package.json
├── multi-agents/             # FastAPI + LangGraph 后端
│   ├── api/                  # 对话、会话、上传接口
│   ├── services/             # 流式会话、对话持久化、上下文压缩
│   ├── graph/                # LangGraph 状态和工作流编排
│   ├── agent_nodes/          # 意图路由、执行、规划、总结节点
│   ├── tools/                # MCP 工具适配和 RAG 工具
│   ├── memory/               # 工作记忆、短期记忆、长期偏好
│   ├── db/                   # PostgreSQL 连接、模型和迁移
│   ├── scripts/              # RAG 知识库构建脚本
│   ├── tests/                # 单元测试和 RAG 评测
│   └── server.py             # FastAPI 服务入口
├── citydata/                 # 景点 CSV 原始数据
└── docs/                     # 项目级技术文档
```

## 前端架构

前端位于 `TripMate/`，主要技术栈如下：

- **Vue 3**：使用 Composition API 和 `<script setup>` 构建页面与组件。
- **TypeScript**：定义 API、消息、会话和流式事件的数据结构。
- **Vite**：负责开发服务器和生产构建。
- **Pinia**：维护当前会话、消息列表、加载状态和流式响应状态。
- **Axios + fetch-event-source**：分别处理普通 HTTP 请求和 SSE 流式对话。
- **Markdown 渲染链路**：支持 Markdown、代码高亮、数学公式和安全过滤。

### 前端核心交互

```text
用户输入问题
    ↓
ChatInputBar
    ↓
Pinia chatbot store
    ↓
POST /chatMessage/stream
    ↓
接收 meta / status / content / error / done 事件
    ↓
更新 ChatHistory 和 ChatMessageItem
```

前端通过 `requestId` 和 `lastChunkId` 支持断线重连。后端保留流式会话中的事件片段，前端重连后可以从上次收到的 chunk 继续回放，避免重复显示或丢失回答内容。

## Agent 编排

后端使用 LangGraph 编排多个 Agent 节点，共享 `GlobalState`。状态中包含用户问题、对话消息、意图上下文、执行结果、总结结果和记忆上下文。

### 工作流

```text
START
  ↓
IntentRouter
  ├─ greeting ───────────────→ FinalOutput → END
  ├─ full_travel 缺少出发地 ─→ FinalOutput → END
  ├─ simple_travel ──────────→ ReactExecutor → Summarizer → FinalOutput → END
  └─ full_travel ────────────→ Planner → StepExecutor → Summarizer → FinalOutput → END
```

### 节点职责

| 节点 | 职责 |
| --- | --- |
| `IntentRouter` | 识别问候、简单出行和完整行程三类意图，提取目的地、出发地、日期、天数、预算和偏好，并加载必要记忆 |
| `ReactExecutor` | 处理简单出行问题，由 ReAct Agent 自主决定是否调用 RAG、天气、POI、路线等工具 |
| `Planner` | 处理完整行程，使用规划模型生成结构化工具执行计划 |
| `StepExecutor` | 按计划逐步调用工具，收集成功结果并记录失败步骤，单个工具失败不会立即中断整个计划 |
| `Summarizer` | 整合 RAG、MCP 和记忆结果，结合用户偏好生成最终旅行建议 |
| `FinalOutput` | 统一收尾、写回记忆，并返回最终回答状态 |

### 两种执行模式

#### 简单出行模式

适合“北京今天下雨吗”“从机场到酒店怎么走”“故宫地址是什么”“去某个景点怎么玩”等日常出行和单点旅行问题。系统进入 `ReactExecutor`，由模型根据工具描述自主选择和调用工具，适合步骤不固定的查询。

#### 完整行程模式

适合“从上海出发去云南玩 7 天，预算 8000 元”的复杂旅游请求。系统先由 `Planner` 生成工具调用计划，再由 `StepExecutor` 顺序执行，最后交给 `Summarizer` 汇总，适合需要多来源信息协同的任务。

## RAG 检索架构

RAG 当前面向景点攻略 chunk 检索，正式数据源是根目录 `citydata/` 下的 CSV 文件。

### 离线建库

```text
citydata/*.csv
    ↓
读取完整景点记录
    ↓
RecursiveCharacterTextSplitter
    ↓ 800 字符，重叠 100 字符
生成 chunk_id
    ↓
qwen3.7-text-embedding
    ↓
ChromaDB + BM25
```

### 在线检索

```text
用户问题
    ↓
LLM 多角度 Query 扩展，最多 3 个 Query
    ↓
向量检索 + BM25
    ↓
RRF 融合，得到最多 10 个候选 chunk
    ↓
qwen3-rerank 精排
    ↓
默认返回最多 5 个 chunk
```

其中，向量检索负责语义匹配，BM25 负责景点名称、专有名词和地址等关键词匹配，RRF 负责融合排序，Rerank 负责对候选结果进行最终相关性排序。RAG 返回的 chunk 会被 Agent 作为工具结果传给总结节点。

详细说明见：[RAG 技术方案](multi-agents/docs/RAG技术方案.md)。

## 工具与数据层

### MCP 工具

`tools/tool_provider.py` 通过 `langchain-mcp-adapters` 连接配置中的 MCP 服务，将远程工具转换为 LangChain Tool，供 ReAct Agent 和 StepExecutor 调用。工具配置位于：

```text
multi-agents/config/servers_config.json
```

当前工具类型包括天气、POI、路线、酒店、火车票、航班、搜索和 RAG 检索等。

### 记忆系统

系统根据场景使用不同层级的记忆：

- **工作记忆**：保存当前任务执行过程中的临时信息。
- **短期记忆**：保存当前会话的近期上下文。
- **长期记忆与用户画像**：保存用户稳定的旅行偏好，例如预算倾向、出行方式、住宿偏好和兴趣类型，并在意图识别和行程总结阶段按需加载。
- **上下文压缩**：历史过长时压缩旧消息，控制 LLM 上下文长度。

用户画像的基本流程如下：

```text
用户对话
    ↓
提取稳定偏好
    ↓
保存到长期记忆
    ↓
后续请求按需加载
    ↓
影响工具查询、行程规划和最终回答
```

### 数据持久化

- PostgreSQL：保存会话、消息和用户相关数据。
- ChromaDB：保存景点 chunk、Embedding 和元数据。
- BM25：当前在 RAG 初始化时根据 ChromaDB 文档重建，主要驻留在内存中。

## 快速开始

### 1. 启动后端

```powershell
cd multi-agents
pip install -r requirements.txt
python server.py
```

后端默认地址：`http://localhost:8000`

开发模式也可以使用：

```powershell
uvicorn server:app --reload
```

### 2. 构建 RAG 知识库

将景点 CSV 文件放入项目根目录 `citydata/`，然后在 `multi-agents/` 目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\rebuild_kb.py
```

首次构建或数据发生整体变化时使用全量重建；网络超时或额度不足导致中断时使用：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\rebuild_kb.py --resume
```

### 3. 启动前端

```powershell
cd TripMate
pnpm install
pnpm dev
```

前端默认由 Vite 提供开发地址，具体端口以终端输出为准。

### 4. 环境变量

在 `multi-agents/.env` 中配置实际使用的服务密钥和基础设施连接信息，至少包括：

```text
DASHSCOPE_API_KEY=...
DEEPSEEK_API_KEY=...
DATABASE_URL=...
```

不同 MCP 服务的地址和开关配置在 `multi-agents/config/servers_config.json` 中维护。不要将 `.env`、API Key 或本地向量数据库提交到 Git。

## 测试与评测

运行后端测试：

```powershell
cd multi-agents
python -m pytest tests/
```

运行 Chunk 级 RAG 评测：

```powershell
.\.venv\Scripts\python.exe -X utf8 tests\eval_rag_chunk.py
```

评测集使用 `relevant_chunk_ids` 标注标准答案，主要统计 `Hit Rate@5`、`Recall@5`、`Precision@5`、`MRR@5` 和 `NDCG@5`。这些指标衡量检索是否命中正确知识片段，不等价于最终自然语言答案的事实准确率。
