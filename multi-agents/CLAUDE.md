# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

智能旅游规划助手（Smart Travel Multi-Agents）—— 基于 LangChain + LangGraph 的多 Agent 架构旅行规划系统。用户提供旅行需求后，系统通过多个协作 Agent 完成意图识别、信息查询（MCP 工具）、RAG 知识检索和方案生成。

## Commands

```bash
# 安装依赖
pip install -r requirements.txt

# 运行 FastAPI 后端（供前端 SPA 调用）
python server.py              # 默认 0.0.0.0:8000
uvicorn server:app --reload   # 等价

# 运行测试
python -m pytest tests/
python -m unittest tests/test_train_query_parsing.py   # 单个测试

# 构建 RAG 知识库（首次部署或文档更新后）
python -c "from tools.rag_tool import get_rag_instance; get_rag_instance().build_knowledge_base('data/dataRAG/docs')"
```

## Architecture

### LangGraph 工作流 (`graph/workflow.py`)

6 节点双路径架构，IntentRouter 统一入口：

```
IntentRouter → ReactExecutor → Summarizer → FinalOutput → END  (简单出行)
IntentRouter → Planner → StepExecutor → Summarizer → FinalOutput → END  (旅游规划)
IntentRouter → FinalOutput → END  (问候/追问直接回答)
```

### Agent 节点 (`agent_nodes/`)

| 节点 | 职责 |
|------|------|
| `intent_router` | 三分类（greeting/simple_travel/full_travel）+ 参数提取 + 分级记忆加载 |
| `react_executor` | ReAct 循环，LLM 自主决策工具调用（简单出行） |
| `planner` | DeepSeek V4 生成 JSON 执行计划（旅游规划） |
| `step_executor` | 按计划逐步调用工具，容错执行 |
| `summarizer` | 汇总工具结果 + 用户偏好，生成最终回答 |
| `final_output` | 记忆晋升（promote Q&A）、状态收尾 |

### 状态管理 (`graph/state.py`)

`GlobalState` 中各节点拥有独立上下文（`intent_context` / `executor_context` / `summarizer_context`），通过 `current_agent` + `is_complete` 控制流转。IntentRouter 通过条件边路由到三条路径。

### 工具层 (`tools/`)

- **ToolProvider** (`tool_provider.py`)：通过 `langchain-mcp-adapters` 连接远程 MCP 服务器（SSE 协议），自动将 MCP 工具转为 LangChain `BaseTool`。全局单例。
- **RAG** (`rag_tool.py`)：ChromaDB + DashScope Embedding，本地向量检索旅游攻略。
- **ContextCompressor** (`context_compressor.py`)：长对话压缩。

### API 层 (`api/` + `services/`)

FastAPI 后端提供 SSE 流式对话端点 `POST /chatMessage/stream`。`chat_service.py` 调用 `travel_graph` 并将节点/工具进度实时推送给前端。

### MCP 服务器 (`config/servers_config.json`)

远程 MCP 工具：12306 火车查询、高德地图（路线/酒店/天气/POI）、八字黄历、必应搜索、航班查询。

## Key Configuration

- `.env` 文件存放 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`LANGCHAIN_API_KEY` 等密钥。
- `config/settings.py` 集中管理模型名称、温度、RAG 参数。
- 执行模型：Qwen3（DashScope）用于规划/对话；DeepSeek V4 用于 Plan-then-Execute 的计划阶段。

## Conventions

- 所有 Agent 节点函数签名统一为 `async def xxx_agent_node(state: GlobalState) -> Dict[str, Any]`。
- 全局单例模式：`get_tool_provider()`、`get_rag_instance()`、`get_profile_manager()`，首次调用时初始化。
- Prompt 模板集中定义在 `config/prompts.py`。
- 用户偏好持久化为 JSON 文件 (`data/user_profiles/`)。
