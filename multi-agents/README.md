# Smart Travel Multi-Agents

基于 LangChain + LangGraph 的多 Agent 智能旅游规划后端，通过 FastAPI 提供 SSE 流式对话接口（`POST /chatMessage/stream`）。用户提供旅行需求后，系统通过多个协作 Agent 完成意图识别、信息查询（MCP 工具）、RAG 知识检索和方案生成。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置密钥（.env 文件）
#    DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / LANGCHAIN_API_KEY 等

# 3. 启动服务（默认 0.0.0.0:8000）
python server.py
# 或 uvicorn server:app --reload
```

## 构建 / 重建 RAG 知识库

知识库基于 `data/dataRAG/docs/` 下的攻略文档（txt / pdf / md / csv），构建为 ChromaDB 向量库 + BM25 索引。**首次部署或文档更新后**需要重建，直接运行脚本：

```bash
python scripts/rebuild_kb.py               # 全量重建（删旧库）
python scripts/rebuild_kb.py --incremental # 增量更新（跳过已导入的 chunks）
python scripts/rebuild_kb.py --source data/dataRAG/docs/other  # 指定文档目录
```

> 脚本内部已处理 stdout 编码（UTF-8），Windows 下直接运行即可，无需设置环境变量。

构建流程：加载文档（PDF 按页拆分）→ 通用切分 + LLM 批量抽取元数据 → ChromaDB 向量入库 → 构建 BM25 索引。

> ⚠️ **常见问题**：若报 `PermissionError: 另一个程序正在使用此文件`（`chroma.sqlite3` 被占用），说明有进程仍持有向量库文件（如运行中的 `server.py` / uvicorn / Jupyter）。先停止该进程再重建；代码已内置重试删除逻辑，会等待句柄释放。

## 目录结构

```
multi-agents/
├── agent_nodes/        # LangGraph 节点（intent_router / planner / step_executor / ...）
├── graph/              # LangGraph 工作流编排
├── tools/
│   ├── rag/            # RAG v2：chunker / retriever(BM25+向量) / reranker / query_transformer / rag_engine
│   ├── rag_tool.py     # 旧接口兼容 shim（勿删，import 路径仍指向它）
│   └── tool_provider.py# MCP 工具适配（SSE 远程 MCP）
├── api/ services/      # FastAPI 接口与服务层
├── config/             # 配置（settings.py / prompts.py / servers_config.json）
└── data/
    ├── dataRAG/docs/   # 攻略文档源（构建知识库的输入）
    └── dataRAG/vectordb/  # ChromaDB 向量库（构建输出）
```
