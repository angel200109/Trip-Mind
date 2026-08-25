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

## 构建 RAG 知识库

数据集：[OpenBayes 景点数据集](https://openbayes.com/console/public/datasets/2SkcDTpplYe/1/overview)。下载并解压后，将其中的 CSV 文件放入项目根目录的 `citydata/` 文件夹。

当前知识库基于项目根目录 `citydata/` 下的城市景点 CSV 文件，切分为 chunk 后构建 ChromaDB 向量库和 BM25 索引。**首次部署或 citydata 数据更新后**需要重建，请在 `multi-agents/` 目录中运行：

```powershell
.\.venv\Scripts\python.exe -u scripts\rebuild_kb.py                 # 全量重建，删除旧向量库
.\.venv\Scripts\python.exe -u scripts\rebuild_kb.py --resume       # 断点续跑，跳过已导入 chunk
.\.venv\Scripts\python.exe -u scripts\rebuild_kb.py --keep-existing # 保留旧库并追加数据
.\.venv\Scripts\python.exe -u scripts\rebuild_kb.py --source D:\path\to\citydata # 指定 CSV 目录
```

脚本默认使用 `qwen3.7-text-embedding` 生成向量，每个 chunk 默认 800 个字符、重叠 100 个字符，并生成 `data/dataRAG/citydata_chunk_catalog.json`。脚本内部已处理 stdout 编码，Windows 下可以直接运行。

构建流程：加载有效景点记录 → 切分 chunk → 分配稳定 `chunk_id` → 调用 Embedding API → 写入 ChromaDB → 构建 BM25 索引。

> ⚠️ **常见问题**：若报 `PermissionError: 另一个程序正在使用此文件`（`chroma.sqlite3` 被占用），说明有进程仍持有向量库文件（如运行中的 `server.py` / uvicorn / Jupyter）。先停止该进程再重建；代码已内置重试删除逻辑，会等待句柄释放。

## 目录结构

```
multi-agents/
├── agent_nodes/        # LangGraph 节点（intent_router / planner / step_executor / ...）
├── graph/              # LangGraph 工作流编排
├── tools/
│   ├── rag/            # RAG：chunker / retriever(BM25+向量) / reranker / query_transformer / rag_engine
│   ├── rag_tool.py     # 旧接口兼容 shim（勿删，import 路径仍指向它）
│   └── tool_provider.py# MCP 工具适配（SSE 远程 MCP）
├── api/ services/      # FastAPI 接口与服务层
├── config/             # 配置（settings.py / prompts.py / servers_config.json）
└── data/
    ├── dataRAG/vectordb/                 # ChromaDB 向量库（本地构建输出）
    └── dataRAG/citydata_chunk_catalog.json # chunk 清单（评测标注使用）
```
