# Citydata Chunk RAG 技术方案

## 一、架构设计

### 1.1 系统定位

本项目的 RAG 用于景点攻略类问答。检索对象不是“一个城市一个文档”，而是 `citydata/` 目录中景点 CSV 记录经过切片后形成的 chunk。

系统主要解决两类问题：

- 根据景点名称查询地址、开放信息、特色和游玩攻略。
- 根据游玩需求查询相关景点攻略片段，并将检索结果交给 LLM 组织答案。

当前 RAG 本身只负责知识检索和结果整理，不负责生成最终回答。

### 1.2 总体流程

#### 离线建库

```text
citydata/*.csv
    ↓
读取完整景点记录
    ↓
生成 LangChain Document
    ↓
按 800 字符切片，重叠 100 字符
    ↓
为每个 chunk 生成稳定唯一的 chunk_id
    ↓
调用 qwen3.7-text-embedding
    ↓
写入 ChromaDB
    ↓
在运行时从 ChromaDB 文档重建 BM25 索引
```

#### 在线检索

```text
用户问题
    ↓
LLM 多角度 Query 扩展，最多生成 3 个查询
    ↓
向量检索 + BM25 检索
    ↓
RRF 融合，得到最多 10 个候选 chunk
    ↓
qwen3-rerank 重排序
    ↓
返回最多 5 个 chunk
    ↓
后续回答模块将 chunk 内容交给 LLM
```

### 1.3 主要模块

| 模块 | 文件 | 作用 |
| --- | --- | --- |
| 建库脚本 | `scripts/rebuild_kb.py` | 读取 citydata、切片、Embedding、写入 ChromaDB |
| CSV 加载 | `tools/rag/citydata_loader.py` | 将完整景点记录转换为原始 Document |
| 文档切片 | `tools/rag/chunker.py` | 使用 RecursiveCharacterTextSplitter 切分文本 |
| 向量存储 | `tools/rag/embedding.py`、ChromaDB | 保存向量、原文和元数据 |
| 混合检索 | `tools/rag/retriever.py` | 执行向量检索、BM25 和 RRF 融合 |
| Query 扩展 | `tools/rag/query_transformer.py` | 生成多个检索角度 |
| 重排序 | `tools/rag/dashscope_reranker.py` | 使用 qwen3-rerank 对候选 chunk 精排 |
| RAG 主入口 | `tools/rag/rag_engine.py` | 串联查询扩展、召回、重排序并返回结果 |
| 评测脚本 | `tests/eval_rag_chunk.py` | 按 chunk_id 评测检索质量 |

### 1.4 数据与文档模型

每条完整的 CSV 景点记录先生成一个原始 `Document`，再被切成一个或多个 chunk。一个景点不一定只有一个 chunk，文本长度超过切片阈值时会产生多个 chunk。

每个 chunk 至少包含：

- `page_content`：当前切片的文本内容。
- `metadata.chunk_id`：chunk 的唯一标识，用于 Chroma、去重和评测。
- `metadata.spot_name`：景点名称。
- `metadata.source_city`：所属城市或地区。
- `metadata.source`：原始 CSV 文件路径。
- `metadata.type`、`rating`、`url` 等 CSV 中已有字段（如果存在）。

评测集使用 `relevant_chunk_ids` 标注正确答案，因此指标针对 chunk，而不是只判断景点名称是否出现。

## 二、细节实现

### 2.1 数据加载与有效性过滤

建库入口是：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\rebuild_kb.py
```

脚本默认扫描仓库根目录的 `citydata/`。当前正式流程支持 CSV 景点数据；已经移除了 RAG 运行时对 `data/dataRAG/docs/` 下 PDF、TXT、MD、CSV 的通用 Loader 导入逻辑。

CSV 加载器会跳过景点名称、正文等关键字段不完整的记录。被跳过的记录不会参与切片、Embedding 或检索。

可用参数：

```text
--source <目录>       指定 CSV 数据目录
--keep-existing       保留已有 Chroma 数据并追加
--resume              保留已有数据，并跳过已存在的 chunk_id
--limit <数量>        只处理前 N 个原始景点记录
--use-proxy           按项目配置启用代理
```

### 2.2 Chunk 切分策略

切片器使用 `RecursiveCharacterTextSplitter`，当前参数为：

```text
chunk_size = 800
chunk_overlap = 100
separators = ["\n\n", "\n", "。", "！", "？", "；", "，", " "]
```

这意味着：

- 目标长度约为 800 个字符，而不是严格保证每个 chunk 都正好 800 个字符。
- 相邻 chunk 通常保留约 100 个字符的重叠，减少上下文在边界处被截断的问题。
- 切分优先尝试段落、换行和中文标点，因此实际长度可能小于或略受边界影响。
- 切片器不会再次调用 LLM，也不会自动把两个景点合并到同一个原始 Document 中。

元数据从原始景点 Document 继承到每个 chunk。当前切片逻辑不再抽取 `area`、`thrill_level` 等旧版字段。

### 2.3 Chunk ID 与目录文件

建库脚本会为 chunk 生成稳定 ID，并处理内容相同导致的重复 ID。每次重建会生成：

```text
data/dataRAG/citydata_chunk_catalog.json
```

该文件是 chunk 目录清单，记录 `chunk_id`、景点、城市、来源和内容预览，方便检查切片结果和制作评测集。它不是向量数据库，也不包含可直接用于相似度计算的向量。

真正的向量、原文和元数据保存在：

```text
data/dataRAG/vectordb/
```

`vectordb/` 是本地构建产物，已加入 Git 忽略规则，不应提交到代码仓库。

### 2.4 Embedding 与 ChromaDB

每个 chunk 单独调用 `qwen3.7-text-embedding` 生成向量，然后写入 ChromaDB。建库脚本默认按 `RAG_BATCH_SIZE=10` 分批请求，降低单次请求过大和网络超时的风险。

Embedding 请求失败时不会产生有效向量。网络超时可能导致脚本中断，因此长时间构建应使用 `--resume` 续跑，并确认当前 API 额度和账户状态正常。

RAG 初始化时从 ChromaDB 读取已有 Document，并在内存中重新构建 BM25 索引。BM25 索引当前不是独立的持久化文件。

### 2.5 混合检索

混合检索同时使用两条通道：

- 向量检索：适合语义相似、表达方式不同的问题。
- BM25：适合景点名称、专有名词、地址和关键词匹配。

默认权重：

```text
向量权重 = 0.5
BM25 权重 = 0.5
RRF k = 60
候选数量 = 10
```

多个 Query 的结果会统一进行 RRF 融合。去重优先使用 `metadata.chunk_id`，只有缺少 chunk_id 时才退回使用来源和正文生成的哈希，避免不同景点正文相同而被错误合并。

### 2.6 Query 扩展

当前开启多角度 Query 扩展：

```text
RAG_ENABLE_QUERY_REWRITE = True
RAG_MULTI_QUERY_COUNT = 3
```

LLM 会从原始问题生成最多 3 个检索表达，例如将“怎么玩”扩展为攻略、路线、注意事项等角度。扩展失败或超过 30 秒时，系统退回使用原始 Query，不会因为扩展失败而完全停止检索。

Query 扩展的作用是提高召回覆盖，不是生成最终答案，也不是重排序。

### 2.7 Rerank 与置信度

混合检索返回最多 10 个候选后，调用 MaaS HTTP 接口：

```text
模型：qwen3-rerank
返回数量：5
请求超时：60 秒
```

Reranker 接收 `query + documents`，对每个候选 chunk 计算相关性分数并重新排序。它属于 Cross-Encoder 风格的精排步骤：模型同时观察问题和候选文本后判断相关性，而不是只比较两个独立向量。

当前置信度标记规则是：

```text
score < RAG_CONFIDENCE_THRESHOLD（默认 0.3）→ [低置信度]
```

这个分数是 reranker 的相关性分数，不是经过校准的概率，因此不能直接解释为“有 73% 的正确率”。Rerank API 失败时，系统会按原候选顺序返回，并使用降级分数 `1 / (i + 1)`。

### 2.8 返回结果与 LLM 输入

`TravelRAG.search()` 默认按 `RAG_SEARCH_K=5` 返回结果，并会保留每个 chunk 的完整 `page_content`。终端日志中的“内容预览”只显示前 80 个字符，不代表传给后续模块的内容只有 80 个字符。

当前还有一个接口默认值差异：

- 直接调用 RAG 搜索，默认使用配置中的 5 条。
- Agent 工具 `rag_search(query, k=3)` 默认返回 3 条；调用方传入 `k=5` 时才返回 5 条。

最终是否把全部返回 chunk 交给 LLM，取决于上层 Agent 的调用逻辑；RAG 检索层本身返回的是 Document 列表或格式化文本。

### 2.9 主要配置

| 配置 | 当前值 | 说明 |
| --- | --- | --- |
| `EMBEDDING_MODEL` | `qwen3.7-text-embedding` | 文本向量模型 |
| `RAG_CHUNK_SIZE` | `800` | 切片目标字符数 |
| `RAG_CHUNK_OVERLAP` | `100` | 相邻切片重叠字符数 |
| `RAG_RETRIEVE_K` | `10` | 混合检索候选数 |
| `RAG_SEARCH_K` | `5` | Rerank 后默认返回数 |
| `RAG_BATCH_SIZE` | `10` | 建库批处理大小 |
| `RAG_VECTOR_WEIGHT` | `0.5` | 向量检索权重 |
| `RAG_BM25_WEIGHT` | `0.5` | BM25 权重 |
| `RAG_RERANK_MODEL` | `qwen3-rerank` | 重排序模型 |
| `RAG_RERANK_TOP_N` | `5` | 精排返回数 |
| `RAG_CONFIDENCE_THRESHOLD` | `0.3` | 低置信度标记阈值 |

### 2.10 Chunk 级评测

评测集位于：

```text
tests/eval_rag_chunk_cases.json
```

每条案例可以标注 1 到多个 `relevant_chunk_ids`。评测程序将检索结果的 `chunk_id` 与标准 ID 集合进行精确匹配，计算：

- `Hit Rate@5`：Top 5 是否至少命中一个相关 chunk。
- `Recall@5`：Top 5 命中的相关 chunk 数 ÷ 该问题相关 chunk 总数。
- `Precision@5`：Top 5 命中的相关 chunk 数 ÷ 5。
- `MRR@5`：第一个相关 chunk 的排名倒数。
- `NDCG@5`：同时考虑命中数量和命中位置。

执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 tests\eval_rag_chunk.py
```

结果输出到终端，并写入被 Git 忽略的 `tests/eval_rag_chunk_results.json`。评测集必须在确定切片策略并生成稳定 chunk_id 后标注；如果切片参数或原始数据变化，应重新标注相关 chunk。

## 三、后续完善方向

### 3.1 优先级较高

- 为每个 chunk 增加稳定的业务主键，避免原始数据排序变化导致目录审查困难。
- 让 BM25 也支持 `source_city` 等元数据过滤，避免混合检索的两条通道过滤行为不一致。
- 对 Embedding 和 Rerank 增加可恢复的请求级重试、断点日志和失败 chunk 清单。
- 固定评测集版本，记录切片参数、Embedding 模型、Rerank 模型和代码版本，保证指标可复现。
- 评测“地址、电话、开放时间”等事实型问题的答案正确性，而不只评测是否召回 chunk。

### 3.2 中期优化

- 对景点名称、地址、电话等字段增加结构化检索或关键词优先策略。
- 对重叠切片进行上下文关联，在召回相邻 chunk 时合并或限制重复内容。
- 根据问题类型动态选择召回数量，例如事实查询少返回，攻略查询扩大候选集合。
- 对 reranker 分数做校准，基于人工标注数据重新确定低置信度阈值。
- 持久化或可快速重建 BM25 索引，减少服务启动时间和内存消耗。

### 3.3 长期方向

- 引入父文档、子 chunk 的层级检索，兼顾精确命中和完整上下文。
- 引入时间有效性字段，处理开放时间、票价和联系方式变化。
- 建立线上检索日志、用户反馈和失败问题回流机制，持续更新评测集。
- 对答案生成增加引用 chunk_id，支持回答溯源和事实核验。

## 四、面试话术

### 4.1 一句话介绍

这是一个面向景点攻略问答的 Chunk 级混合 RAG：离线将 CSV 景点记录按 800/100 切片并向量化，在线通过多 Query 扩展、向量检索、BM25、RRF 融合和 qwen3-rerank 精排，最后把 Top-K 相关 chunk 交给 LLM 生成回答。

### 4.2 典型问题与回答

**问：为什么不把一个城市或一个景点作为一个 Document？**

答：整城市文档粒度过粗，查询地址或攻略时会把大量无关内容带入上下文；整景点一个 Document 又可能过长。当前先保留景点记录作为父级来源，再按 800 字符切成多个 chunk，在召回精度和上下文完整性之间折中。

**问：为什么使用混合检索？**

答：向量检索擅长语义相似，BM25 擅长景点名称、专有名词和地址等精确词匹配。两者通过 RRF 融合，可以降低单一检索方式的漏召回风险。

**问：Query 扩展和 Rerank 分别解决什么问题？**

答：Query 扩展扩大检索表达，解决“用户问法和原文用词不同”导致的召回不足；Rerank 重新比较问题与候选 chunk 的相关性，解决候选集中排序不准确的问题。

**问：Rerank 返回 5 条，是不是只检索了 5 条？**

答：不是。当前先通过混合检索得到最多 10 个候选，再由 qwen3-rerank 排序并返回前 5 个。10 是候选池大小，5 是最终结果数量，评测中的 `@5` 也表示只看前 5 个结果。

**问：一个问题只有一个相关 chunk 时，Recall@5 如何计算？**

答：如果这个唯一相关 chunk 出现在 Top 5，Recall@5 就是 1，也就是 100%；如果没有命中，就是 0。多个相关 chunk 时，Recall 是命中的相关 chunk 数除以标准答案 chunk 总数。

**问：低置信度标签代表什么？**

答：它表示 reranker 分数低于当前阈值 0.3，只是相关性风险提示，不是经过概率校准的正确率。若要把它用于自动拒答，需要用人工标注数据校准阈值。

**问：如何保证评测结果有效？**

答：先固定切片策略并生成稳定 chunk_id，再标注每个问题对应的 relevant_chunk_ids，最后运行 chunk 级评测。这样评测的是检索是否命中了正确知识片段，而不是只看景点名称是否相同。

**问：当前方案有哪些不足？**

答：BM25 目前在启动时从 Chroma 文档重建，未单独持久化；混合检索的元数据过滤主要作用于向量通道；Embedding 和 Rerank 依赖外部 API，可能受额度、网络和超时影响；另外，当前指标主要反映检索命中情况，尚未完全覆盖最终答案的事实正确性。

### 4.3 面试时应明确的边界

- 目前正式数据源是 `citydata/*.csv`，不是旧的 `data/dataRAG/docs/` 通用文档目录。
- 当前 chunk 切片不调用 LLM 做元数据抽取。
- 当前 RAG 使用 `qwen3.7-text-embedding` 做向量化，使用 `qwen3-rerank` 做精排。
- `Hit Rate@5` 和 `Recall@5` 是检索指标，不等价于最终答案准确率。
- 评测结果依赖评测集标注质量、chunk 策略、模型版本和数据版本，不能脱离这些条件单独比较。
