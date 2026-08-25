# RAG 混合检索系统技术文档

## 一、架构设计

### 1. 功能背景

在智能旅游规划助手中，Agent 需要基于旅游攻略知识库回答用户关于景点、美食、交通、住宿等方面的问题。传统的纯向量检索存在以下问题：
- 用户口语化查询与知识库文档的表达差异导致召回率低
- 单一向量相似度无法精确衡量相关性
- 缺少对中文关键词的精确匹配能力

因此设计了 **混合检索 + Rerank** 的 RAG pipeline，融合语义检索和关键词检索的优势，再通过专业重排序模型精排，显著提升检索质量。

### 2. 整体架构

```
用户查询
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│                   RAG Engine (TravelRAG)                   │
│                                                           │
│  ① Query Transformer                                      │
│     LLM 扩展为 N 个变体查询                                │
│            │                                              │
│            ▼                                              │
│  ② Hybrid Retriever                                       │
│     ┌──────────────┬──────────────┐                       │
│     │ BM25 (jieba) │ Vector (Chroma)│                     │
│     └──────┬───────┴──────┬───────┘                       │
│            │    RRF 融合   │                               │
│            └──────┬───────┘                               │
│                   ▼                                        │
│  ③ DashScope Reranker (gte-rerank)                        │
│     精排 + 置信度标记                                       │
│            │                                              │
│            ▼                                              │
│  ④ 格式化输出（带 metadata + 分数）                         │
└──────────────────────────────────────────────────────────┘
  │
  ▼
Agent 节点使用检索结果生成回答
```

### 3. 模块划分

| 模块 | 文件 | 职责 |
|------|------|------|
| RAG Engine | `tools/rag/rag_engine.py` | 编排整个 pipeline，对外提供统一接口 |
| Query Transformer | `tools/rag/query_transformer.py` | LLM 多角度查询扩展 |
| Hybrid Retriever | `tools/rag/retriever.py` | BM25 + Vector 混合检索 + RRF 融合 |
| Reranker | `tools/rag/reranker.py` | DashScope qwen3-rerank 精排 |
| Chunker | `tools/rag/chunker.py` | citydata 景点文档切分 |

### 4. LLM / Tool / RAG / Memory 分工

- **LLM (Qwen3)**：Query 扩展
- **Embedding (DashScope)**：文档向量化，ChromaDB 存储
- **BM25 (rank_bm25 + jieba)**：中文关键词精确匹配
- **Reranker (DashScope qwen3-rerank)**：Cross-encoder 精排
- **ChromaDB**：向量持久化存储，支持 metadata 过滤

### 5. 数据流

```
构建阶段:
  citydata CSV → 景点 Document → chunk_documents → Embedding → ChromaDB 入库 + BM25 索引

检索阶段:
  用户 query → expand_queries (LLM生成N个变体)
            → 每个变体同时走 BM25 + Vector 检索
            → RRF 融合去重 → top-K 候选
            → DashScope Rerank 精排
            → 格式化输出 (带分数 + 置信度标记)
```

---

## 二、细节实现

### 1. 核心执行流程

`TravelRAG.search()` 是对外唯一的检索入口，完整 pipeline 如下：

```python
async def search(query, k=3, filters=None):
    # 1. Query 扩展: LLM 生成 3 个不同角度的变体查询
    queries = await query_transformer.expand_queries(query, n=3)
    
    # 2. 混合检索: 每个 query 同时走 BM25 + Vector
    candidates = await hybrid_retriever.retrieve(queries, k=10, filters=filters)
    
    # 3. Rerank: 精排 + 置信度过滤
    ranked = await reranker.rerank(query, candidates)
    
    # 4. 格式化输出
    return format_results(ranked[:k])
```

### 2. 关键技术实现

#### (1) Multi-Query 扩展

通过 LLM 将单一查询扩展为多个不同角度的变体，提高召回覆盖率：

```python
# query_transformer.py
RAG_MULTI_QUERY_PROMPT = """生成 {n} 个不同角度的检索查询：
- 覆盖同义词和不同表达方式
- 从不同角度切入（具体项目名、体验类型、适合人群等）
- 保持与原始查询的语义相关性"""
```

设计要点：
- 始终保留原始 query 在扩展列表中，保证最低召回
- 清洗 LLM 输出中的编号前缀，提取纯净查询文本
- 扩展失败时 graceful degradation 回退到原始查询

#### (2) BM25 + Vector 混合检索 + RRF 融合

```python
# retriever.py - 核心融合逻辑
for query in queries:
    v_docs = vector_search(query, k=10)   # ChromaDB 语义检索
    b_docs = bm25_search(query, k=10)     # jieba 分词 + BM25Okapi

# RRF (Reciprocal Rank Fusion) 融合
for h, (doc, rank) in vector_results.items():
    scores[h] += vector_weight / (rank + rrf_k)  # rrf_k=60
for h, (doc, rank) in bm25_results.items():
    scores[h] += bm25_weight / (rank + rrf_k)
```

设计要点：
- **jieba 搜索模式分词**：`cut_for_search` 同时输出全词和细粒度子词，兼顾精确匹配和召回
- **RRF 融合公式**：`score = weight / (rank + k)`，k=60 是经验值，平滑排名差异
- **多 query 去重**：同一文档在多个 query 中出现时取最优排名
- **content hash 去重**：MD5 前 16 位作为文档唯一标识
- **metadata 过滤**：支持 area、thrill_level 等条件，构建 ChromaDB `$and` where 子句

#### (3) DashScope Reranker 精排

```python
# reranker.py
response = TextReRank.call(
    model="gte-rerank",
    query=query,
    documents=texts,
    top_n=len(documents),  # 全部打分
    api_key=api_key,
)
```

设计要点：
- 使用原始 query（非扩展版）做 rerank，保持与用户意图的一致性
- 输出带 `low_confidence` 标记，阈值 0.3 以下标记为低置信度
- API 调用失败时 graceful fallback 到 `1/(rank+1)` 的倒排分数

#### (4) 文档切分 + LLM 元数据抽取

```python
# chunker.py - 构建阶段
splitter = RecursiveCharacterTextSplitter(
    separators=["\n\n", "\n", "。", "！", "？", "；", "，", " "],
    chunk_size=800, chunk_overlap=100
)
```

LLM 批量抽取元数据（source_city、area、type、thrill_level）：
- 批量处理减少 API 调用次数（batch_size=10）
- 校验枚举值合法性，非法值清空
- 单条/批量两种模式，单条 chunk 走独立 prompt

### 3. 异常与性能

| 环节 | 异常处理 | 降级策略 |
|------|---------|---------|
| Query 扩展 | try/except | 回退到原始 query |
| BM25 索引未就绪 | is_ready 检查 | 仅使用向量检索 |
| Reranker API 失败 | status_code 检查 | 返回原始排序 + 倒排分数 |
| LLM 元数据抽取 | JSON 解析异常 | 跳过该批次，保留空 metadata |

性能优化：
- **延迟初始化**：QueryTransformer、HybridRetriever、Reranker 首次使用时才创建
- **全局单例**：`get_rag_instance()` 避免重复加载向量库
- **asyncio.to_thread**：将同步的 ChromaDB 查询和 DashScope API 调用放入线程池，不阻塞事件循环
- **内容 hash 去重**：避免重复文档浪费 rerank 配额

### 4. 技术难点

1. **中文混合检索的分词策略**：jieba `cut_for_search` 在 BM25 场景下比 `cut` 模式效果更好，因为它同时保留了完整词和子词，兼顾了精确匹配和模糊匹配
2. **Multi-Query + RRF 的交互**：多个扩展 query 各自独立检索后需要跨 query 去重 + 取最优排名，而非简单合并
3. **Rerank 用原始 query 而非扩展 query**：扩展 query 的目的是提高召回，但最终相关性判断应以用户原始意图为准
4. **构建时 LLM 元数据抽取的批量化**：平衡 API 调用成本和输出解析可靠性，10 条一批是经验值

---

## 三、未来改进方向

### 1. 架构优化

**当前**：BM25 索引在内存中，重启丢失需重建  
**改进**：持久化 BM25 索引（pickle 序列化或 Elasticsearch 替代）  
**收益**：冷启动时间从秒级降到毫秒级

**当前**：单一 embedding 模型处理所有类型查询  
**改进**：根据查询类型选择不同检索策略（短关键词偏 BM25 权重，长句偏向量权重）  
**收益**：动态权重分配提升不同类型查询的准确率

### 2. 性能与成本

**当前**：每次检索都调用 LLM 做 query 扩展 + 调用 Reranker API  
**改进**：添加 query 缓存（LRU），相似查询复用已有扩展结果；对高频查询预计算结果  
**收益**：减少约 60% 的 LLM/API 调用，降低延迟和成本

**当前**：Reranker 对全部候选文档打分  
**改进**：先用轻量级分数过滤明显不相关的文档（如 RRF 分数低于阈值），再送入 Reranker  
**收益**：减少 Reranker 输入长度，降低延迟和 token 消耗

### 3. RAG 检索优化

**当前**：固定 chunk_size=800，不区分文档类型  
**改进**：实现语义切分（Semantic Chunking），基于段落语义边界动态切分  
**收益**：避免将完整信息切断在两个 chunk 中间，提升检索精度

**当前**：Multi-Query 扩展是纯 LLM 生成，可能偏离用户意图  
**改进**：引入 HyDE (Hypothetical Document Embeddings)，先让 LLM 生成假设性答案文档，再用答案文档做向量检索  
**收益**：缩小 query 与文档的语义鸿沟

**当前**：metadata 过滤仅支持精确匹配  
**改进**：支持 LLM 自动从查询中提取过滤条件（Self-Query Retriever 模式）  
**收益**：用户无需手动指定过滤参数，系统自动结构化查询

### 4. 稳定性与评估

**当前**：无检索质量评估指标  
**改进**：建立评估数据集（query → expected docs），定期跑 recall@k、MRR、NDCG  
**收益**：量化检索质量，指导参数调优

**当前**：pipeline 环节间无可观测性  
**改进**：接入 LangSmith tracing，记录每个环节的输入输出、延迟、token 用量  
**收益**：快速定位检索质量下降的瓶颈环节

---

## 四、面试话术

### 1. 30 秒介绍

> 我在项目中设计了一套旅游知识库的 RAG 检索系统。核心思路是 **Multi-Query 扩展 + BM25/向量混合检索 + Rerank 精排** 的三阶段 pipeline。先用 LLM 把用户口语化查询扩展为多个检索角度，然后同时走关键词检索和语义向量检索，用 RRF 算法融合两路结果，最后通过 DashScope 的 cross-encoder 重排序模型精排。这套方案相比纯向量检索，在中文旅游场景下召回率和精排质量都有明显提升。

### 2. 1～2 分钟完整介绍

> **业务背景**：我们的旅游助手需要从攻略知识库中检索信息辅助规划。早期用纯向量检索，发现两个问题：一是用户口语化表达和文档差异大导致召回不足，二是向量相似度排序不够精准。
>
> **架构设计**：我设计了四阶段的 pipeline。第一阶段是 Query Transformer，用 Qwen3 将用户查询扩展为 3 个不同角度的变体，覆盖同义词和不同切入点。第二阶段是 Hybrid Retriever，每个变体同时走 BM25 关键词检索和 ChromaDB 向量检索，用 jieba 搜索模式做中文分词，然后通过 RRF 算法融合两路排名。第三阶段用 DashScope 的 gte-rerank 模型做 cross-encoder 精排，输出带置信度分数。第四阶段格式化输出。
>
> **技术难点**：主要有三个。一是 RRF 融合时多 query 的去重策略，同一文档在不同 query 中被检索到时取最优排名；二是 Rerank 阶段刻意使用原始 query 而非扩展 query，因为扩展的目的是提高召回而不是改变相关性判断的锚点；三是构建阶段用 LLM 批量抽取结构化 metadata，支持按区域和类型过滤检索。
>
> **最终效果**：整个系统支持全链路降级，任何环节失败不会阻塞检索。通过配置化参数可以灵活调整 BM25/向量权重、rerank 阈值等。

### 3. 技术亮点

1. **Multi-Query + RRF 混合检索**：通过 LLM 多角度扩展查询，结合 BM25 精确匹配和向量语义匹配，用 RRF 融合两路结果，在中文旅游场景下兼顾召回率和精确度
2. **全链路 Graceful Degradation**：每个环节（query 扩展、BM25、reranker）都有独立的异常处理和降级策略，确保系统可用性
3. **构建时 LLM 元数据抽取**：不依赖特定文档格式，通过 LLM 批量提取结构化 metadata，使知识库支持多维度过滤检索
4. **延迟初始化 + 单例模式**：子组件按需创建，避免冷启动时大量初始化开销；全局单例避免重复加载向量库
5. **异步非阻塞设计**：ChromaDB 查询和外部 API 调用通过 `asyncio.to_thread` 放入线程池，不阻塞 Agent 的事件循环

### 4. 面试官可能追问

1. 为什么选择 BM25 + 向量混合检索，而不是纯向量？
2. RRF 融合的 k 值为什么是 60？
3. 为什么 Rerank 用原始 query 而不是扩展后的 query？
4. Multi-Query 扩展会不会引入噪声？如何控制？
5. BM25 索引在内存中，重启怎么办？
6. 如何评估 RAG 检索的效果？
7. chunk size 800 是怎么确定的？
8. 如果知识库规模增大 10 倍，架构需要怎么调整？
9. DashScope Reranker 的延迟和成本如何？有没有替代方案？
10. 为什么不用 LangChain 内置的 EnsembleRetriever？

### 5. 重点问题参考答案

#### Q: 为什么选择 BM25 + 向量混合检索？

**考察点**：对检索技术的理解深度，是否能说清两种方法的互补性。

**推荐回答**：
> 纯向量检索擅长语义相似匹配，但对精确关键词（如景点名"欢乐嘉年华"、设备名"过山车"）的匹配可能不如词级别的 BM25。旅游查询中经常包含这种精确实体，BM25 能补充向量检索在这方面的不足。同时向量检索能处理同义表达（如"刺激项目"和"惊险游乐设施"），两者互补。RRF 融合的好处是不需要对两种检索结果做分数归一化，直接用排名位置计算，简单高效。

**继续追问**：BM25 和向量的权重 0.5/0.5 是怎么确定的？
> 目前是经验值。理想方案是在评估数据集上做 grid search，分别测试不同权重组合下的 recall@k 和 NDCG，找到最优配比。我们还留了配置项可以按需调整。

#### Q: Rerank 为什么用原始 query 而不是扩展 query？

**考察点**：对 RAG pipeline 中各环节职责划分的理解。

**推荐回答**：
> Multi-Query 扩展的目标是提高召回率——通过不同角度的表达把更多潜在相关文档找出来。但 Rerank 的目标是判断文档与用户**真实意图**的相关性。如果用扩展后的 query 做 rerank，可能会偏向某个扩展角度而偏离用户本意。所以召回阶段用多个 query 广撒网，精排阶段回到原始 query 做精确判断，各司其职。

#### Q: 如何评估 RAG 检索的效果？

**考察点**：工程成熟度，是否有量化意识。

**推荐回答**：
> 目前项目中有 `tests/eval_rag_recall.py` 做基础的召回评估。完整的评估方案应该包括：构建标注数据集（query → 期望返回的文档），然后计算 Recall@K（K 条结果中包含正确答案的比例）、MRR（正确答案第一次出现的位置倒数）、NDCG（考虑排序位置的折扣累积增益）。可以分阶段评估：检索阶段的召回率、rerank 后的 NDCG、最终生成回答的准确性。理想情况下接入 CI 自动化运行，参数变更时及时发现回归。

#### Q: 如果知识库规模增大 10 倍，架构需要怎么调整？

**考察点**：对系统扩展性的思考。

**推荐回答**：
> 几个方向。一是向量库从 ChromaDB 换成 Milvus 或 Qdrant 这类支持分布式的向量数据库。二是 BM25 换成 Elasticsearch，支持持久化索引和分布式检索。三是 Reranker 的候选数量需要控制，可以在 RRF 融合后先用分数阈值过滤一轮再送入 Reranker。四是 Multi-Query 扩展可以加缓存，相似查询复用结果。五是考虑引入文档层级的粗排（如按 metadata 先过滤大范围），减少细粒度检索的规模。
