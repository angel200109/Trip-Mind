# 智慧出行 Agent 架构编排技术文档

## 一、架构设计

### 1. 功能背景

智慧出行助手需要处理从日常出行问题（"杭州天气怎么样"、"上海到杭州的火车票"）到完整行程规划（"从上海去杭州2天，预算1500，帮我安排行程"）的多种场景。传统单一 LLM 方案无法同时兼顾响应速度和规划深度——日常出行问题不需要重规划流程，完整行程规划又需要多步工具调用和结构化推理。

因此，系统采用 **6 节点双路径** LangGraph 编排架构：一条 ReAct 快速路径处理日常出行查询，一条 Plan-then-Execute 深度路径处理完整行程规划，通过 Intent Router 在入口统一分流。

### 2. 整体架构

```
                          ┌─ greeting ──────────────────────────────────── final_output → END
                          │
START → intent_router ────├─ simple_travel → react_executor ─────────┐
                          │                                          ├─ summarizer → final_output → END
                          │  ┌─ 缺少出发地 → final_output → END       │
                          └─ full_travel ┤                            │
                                         └─ 参数完整 → planner → step_executor ─┘
```

**双模式路由**：

| 模式 | 触发条件 | 路径 | 特点 |
|------|---------|------|------|
| 快速模式 | 日常出行问题（天气、交通、路线、景点） | intent → react_executor → summarizer → output | ReAct 自主决策工具调用，响应快 |
| 深度模式 | 完整行程规划（含出发地、目的地、天数等） | intent → planner → step_executor → summarizer → output | 结构化 JSON 计划 + 顺序执行，规划全面 |
| 直答模式 | 问候/闲聊 | intent → output | 无工具调用，直接回复 |

### 3. Agent / Workflow

系统基于 **LangGraph StateGraph** 构建，6 个节点各司其职：

| 节点 | 职责 | 使用的 LLM |
|------|------|-----------|
| `intent_router` | 三分类意图识别 + 参数提取 + 记忆加载 | Qwen3 (结构化输出) |
| `react_executor` | ReAct 自主循环，LLM 自决工具调用 | Qwen3 (create_react_agent) |
| `planner` | 生成结构化 JSON 执行计划 | DeepSeek R1 (推理模型) |
| `step_executor` | 确定性顺序执行计划中的每个步骤 | 无 LLM（纯工具调用） |
| `summarizer` | 聚合工具结果 + 个性化出行建议生成 | Qwen3 (流式输出) |
| `final_output` | 统一输出 + 记忆回写 | 无 LLM（触发 promotion） |

### 4. LLM / Tool / RAG / Memory

**双 LLM 策略**：
- **Qwen3** (temperature=0.7)：负责交互性任务（分类、对话、汇总），追求自然流畅
- **DeepSeek R1** (temperature=0.1)：负责推理性任务（计划生成），追求逻辑严密

**Tool 层**（基于 MCP 协议）：
- 高德天气/酒店/POI/路线规划
- 12306 火车票查询
- 航班查询
- 黄历日期查询
- Bing 搜索
- RAG 知识库检索

**RAG 系统**（混合检索 + Rerank）：
- BM25 关键词检索 + 向量语义检索 → RRF 融合 → DashScope Rerank

**三层记忆系统**：
- Working Memory（进程内 dict，请求级）
- Short-Term Memory（Redis，会话级，20 轮 / 30 分钟）
- Long-Term Memory（PostgreSQL 用户偏好 + ChromaDB 知识库，用户级）

### 5. 数据流

```
用户消息 → FastAPI SSE 端点
    ↓
构建 GlobalState（历史消息 + 当前查询 + session 信息）
    ↓
LangGraph astream_events 执行
    ↓
intent_router: 加载记忆 → LLM 结构化分类 → 路由决策
    ↓                    ↓                      ↓
  [greeting]        [simple_travel]         [full_travel]
  直接回复          react_executor           planner (R1)
                    (自主工具循环)           step_executor (顺序执行)
    ↓                    ↓                      ↓
                    ─────────────────────────────────
                              ↓
                         summarizer（聚合 + 个性化出行建议）
                              ↓
                         final_output
                    ├─ 回写 Redis 短期记忆
                    ├─ 提取 + 更新 PG 用户偏好
                    └─ 保存对话到 PG
                              ↓
                    SSE 流式推送至前端
```

---

## 二、细节实现

### 1. 核心执行流程

#### 1.1 Intent Router —— 入口统一分类

```python
# agent_nodes/intent_router.py
class IntentClassification(BaseModel):
    query_type: Literal["greeting", "simple_travel", "full_travel"]
    destination: str
    origin: str
    travel_days: int
    budget: float
    travel_date: str
    preferences: List[str]
```

核心实现要点：
- 使用 Pydantic 结构化输出（`with_structured_output`），避免 JSON 解析失败
- 多目的地检测：正则匹配 "再去/然后去/接着去" 等关键词，识别多段行程
- 排除误判：识别 "往返/来回" 等非多目的地模式
- 异步加载三层记忆：Working → Short-Term → Long-Term，互不阻塞

#### 1.2 双路径核心差异

**路径 A：ReAct 快速路径** (`react_executor`)
```python
# 基于 LangGraph prebuilt 的 create_react_agent
agent = create_react_agent(llm, tools)
# LLM 自主决定：调用哪些工具、何时停止
# 适合日常出行的单点查询
```

**路径 B：Plan-then-Execute 深度路径** (`planner` + `step_executor`)
```python
# Planner 输出结构化 JSON 计划
{
  "query_plan": [
    {"tool": "rag_search", "params": {"query": "杭州出行攻略"}, "description": "..."},
    {"tool": "gaode_weather", "params": {"city": "杭州"}, "description": "..."},
    {"tool": "train_query", "params": {...}, "description": "..."}
  ]
}

# StepExecutor 确定性执行（无 LLM 参与）
for step in plan_steps:
    tool = tool_map[step["tool"]]
    result = await tool.ainvoke(step["params"])
```

#### 1.3 路由决策逻辑

```python
def route_after_intent_router(state: GlobalState) -> Literal["react_executor", "planner", "final_output"]:
    if state.get("is_complete", False):
        return "final_output"  # greeting 或需要追问的情况
    intent_ctx = state.get("intent_context") or {}
    query_type = intent_ctx.get("query_type", "greeting")
    if query_type == "simple_travel":
        return "react_executor"
    elif query_type == "full_travel":
        return "planner"
    else:
        return "final_output"
```

### 2. 关键技术实现

#### 2.1 State 设计 —— 节点间解耦通信

```python
class GlobalState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]  # 累加式消息历史
    user_query: Optional[str]
    intent_context: Optional[IntentContext]        # intent_router 写入
    executor_context: Optional[ExecutorContext]    # executor 写入
    summarizer_context: Optional[SummarizerContext] # summarizer 写入
    memory_context: Optional[Dict[str, Any]]      # 记忆上下文
    is_complete: bool                             # 流程终止标志
    final_answer: Optional[str]                   # 最终回答
```

**设计要点**：
- 每个节点有独立的 context 字段，避免写入冲突
- `messages` 使用 `operator.add` 注解，支持累加而非覆盖
- `is_complete` 作为全局终止信号，任何节点可提前终止流程

#### 2.2 RAG 混合检索管线

```
用户查询
  ↓
QueryTransformer（扩展为 3 个变体查询）
  ↓
┌─ BM25 检索（jieba 分词 → 关键词匹配）
├─ Vector 检索（ChromaDB 语义相似度）
└─ RRF 融合（权重 0.5:0.5）
  ↓
DashScope Rerank（gte-rerank 模型）
  ↓
Top-3 结果（置信度阈值 0.3）
```

#### 2.3 记忆回写管线（Memory Promotion）

```python
async def promote(session_id, user_id, user_msg, assistant_msg):
    # 1. 写入 Redis 短期记忆
    await short_term.add_message(session_id, "user", user_msg)
    await short_term.add_message(session_id, "assistant", assistant_msg)
    
    # 2. 快速正则提取偏好
    prefs = regex_extract(user_msg)  # 预算(\d+), 喜欢(XX), 不喜欢(XX)
    
    # 3. LLM 深度提取（5s 超时，非阻塞）
    if not prefs:
        prefs = await asyncio.wait_for(llm_extract(user_msg), timeout=5.0)
    
    # 4. 更新 PG（数组字段追加，标量字段覆盖）
    await long_term.update_preferences(user_id, **prefs)
```

#### 2.4 SSE 流式传输与断线重连

```python
# 事件拦截策略
async for event in graph.astream_events(state, version="v2"):
    if event["event"] == "on_chain_start":
        yield status_chunk("正在分析您的需求...")
    elif event["event"] == "on_tool_start":
        yield status_chunk(f"正在{tool_status_map[tool_name]}...")
    elif event["event"] == "on_chat_model_stream":
        yield content_chunk(token)

# 断线重连：缓冲所有 chunk，客户端带 lastChunkId 重连时 replay
```

### 3. 异常与性能

**容错策略 —— 逐层降级**：

| 组件 | 异常场景 | 降级方案 |
|------|---------|---------|
| IntentRouter | LLM 调用失败 | 默认归类为 greeting，直接回复 |
| Planner | JSON 解析失败 | 使用兜底计划（rag_search + gaode_weather） |
| StepExecutor | 工具不存在/执行失败 | 记录失败，继续执行下一步 |
| RAG Rerank | API 超时 | 使用 rerank 前的排序结果 |
| Redis | 连接失败 | 降级为进程内 dict |
| 偏好提取 LLM | 超过 5s | 跳过本次提取，不阻塞响应 |

**性能特征**：
- 简单查询端到端延迟：2-4s（1次LLM分类 + 2-3次工具 + 1次汇总）
- 完整规划端到端延迟：5-10s（1次分类 + 1次R1规划 + 4-6次工具 + 1次汇总）
- 记忆加载：<50ms（working） / 50-200ms（Redis/PG）
- RAG 检索：200-400ms（含 rerank）

### 4. 技术难点

**难点 1：双路径如何统一 State**
- 挑战：ReAct 路径的工具结果是 LLM 自驱产生的，Plan-then-Execute 路径是确定性执行的，两者产出结构不同
- 方案：`ExecutorContext` 统一定义 `tool_results` 和 `collected_info` 字段，两条路径都写入相同格式，summarizer 无需区分来源

**难点 2：多目的地行程的正确拆分**
- 挑战："从上海去杭州玩两天再去南京" 需要识别为多段行程，而 "从杭州到上海来回" 不应被误判
- 方案：正则匹配多目的地关键词 + 逗号分隔城市检测 + 往返/来回排除规则 + scenario_type 标记传递给下游

**难点 3：ReAct Agent 的工具结果提取**
- 挑战：`create_react_agent` 内部状态对外不透明，需要从消息流中提取结构化工具结果
- 方案：遍历 agent 输出的 messages，识别 `ToolMessage` 类型，解析 tool_call_id 关联参数与结果

**难点 4：Plan 生成的质量与格式控制**
- 挑战：LLM 生成的 JSON 计划可能格式错误、工具名拼错、参数缺失
- 方案：使用低温 DeepSeek R1 + prompt 中列出完整工具清单与参数格式 + JSON 解析失败时使用兜底计划

---

## 三、未来改进方向

### 1. 架构

**当前问题**：StepExecutor 顺序执行所有计划步骤，独立工具之间无并行  
**如何改进**：分析步骤依赖关系，构建 DAG 图，将无依赖步骤并行执行  
**预期收益**：完整规划延迟从 5-10s 降至 3-5s（多个独立工具并行调用）

**当前问题**：Planner 输出的计划是一次性的，无法根据中间结果动态调整  
**如何改进**：引入 Adaptive Planning，StepExecutor 执行后判断是否需要追加步骤（如酒店搜不到则换关键词）  
**预期收益**：提升规划完整性和容错能力

### 2. 性能与成本

**当前问题**：每次请求都完整执行分类 → 工具 → 汇总全流程  
**如何改进**：引入结果缓存（相同目的地+日期的天气/景点短期缓存）  
**预期收益**：重复查询延迟降低 60%，减少外部 API 调用成本

**当前问题**：Summarizer 每次都将全部工具结果传入 LLM  
**如何改进**：对工具结果做预处理压缩（提取关键字段），减少输入 token  
**预期收益**：汇总阶段 token 消耗降低 30-50%

### 3. RAG / Memory

**当前问题**：RAG 知识库为静态文档，不会根据用户反馈更新  
**如何改进**：将用户对出行建议的正负反馈写入知识库（用户点评 → 新文档 chunk）  
**预期收益**：建议质量随用户使用逐步提升

**当前问题**：长期记忆的偏好字段固定，无法捕获复杂偏好模式  
**如何改进**：从固定 schema 升级为 embedding-based 偏好向量（用户画像向量化）  
**预期收益**：支持更细粒度的个性化出行建议

### 4. 稳定性与评估

**当前问题**：缺乏系统性的 Agent 质量评估  
**如何改进**：建立评估数据集 + 自动化评估流水线（意图分类准确率、计划完整度、最终回答质量）  
**预期收益**：量化迭代效果，避免改一处坏一片

**当前问题**：LLM 调用无重试策略（除了最终降级）  
**如何改进**：引入指数退避重试 + 多 provider fallback（Qwen3 失败切 GPT-4o-mini）  
**预期收益**：提升系统可用性至 99.9%+

---

## 四、面试话术

### 1. 30 秒介绍

> 我做的是一个智慧出行 Agent，基于 LangGraph 实现了 6 节点双路径编排架构。系统能自动识别用户意图，对日常出行问题走 ReAct 快速路径让 LLM 自主决策工具调用，对完整行程规划走 Plan-then-Execute 深度路径用推理模型生成结构化计划再顺序执行。配合混合检索 RAG 和三层记忆系统，实现了个性化的实时流式出行服务。

### 2. 1～2 分钟介绍

> **业务背景**：出行场景中用户需求差异大——问天气、查火车票只需一次工具调用，规划多日行程需要查交通、酒店、景点、天气等多个维度。单一处理方式要么过重要么不够深。
>
> **Agent 架构**：我设计了 6 节点双路径的 LangGraph 工作流。入口是 Intent Router，用 Qwen3 做三分类（greeting/simple_travel/full_travel）加参数提取，同时异步加载三层记忆。然后根据分类走两条不同路径：
>
> - 日常出行问题走 ReAct 路径，用 create_react_agent 让 LLM 自主决定调什么工具、调几次；
> - 完整行程规划走 Plan-then-Execute 路径，用 DeepSeek R1 推理模型生成 JSON 执行计划，再由 StepExecutor 确定性顺序执行。
>
> 两条路径最终汇聚到 Summarizer，结合用户偏好生成个性化出行建议。
>
> **核心实现**：工具层基于 MCP 协议接入高德/12306/航班等服务；RAG 系统用 BM25+向量混合检索加 Rerank；记忆分三层——进程内工作记忆、Redis 会话记忆、PG 长期偏好。全流程 SSE 流式输出，支持断线重连。
>
> **技术难点**：一是双路径的 State 统一，两条路径产出结构不同但 Summarizer 需要统一处理；二是多目的地检测，需要区分 "上海去杭州再去南京" 和 "杭州上海来回"；三是容错设计，任何节点失败都有降级方案，确保用户一定能收到回复。
>
> **最终效果**：日常出行查询 2-4 秒响应，完整行程规划 5-10 秒出结果，支持用户偏好积累和个性化出行建议。

### 3. 技术亮点

1. **双 LLM 策略分工**：Qwen3 负责交互（高温、自然）+ DeepSeek R1 负责推理（低温、严密），各取所长而非一个模型打天下

2. **Plan-then-Execute 分离设计**：计划生成与执行解耦，计划是可审计的 JSON 结构，执行是确定性的——方便调试、方便复现、方便观测

3. **混合检索 + Rerank 管线**：BM25 抓精确匹配 + 向量抓语义关联 + RRF 融合 + DashScope Rerank 精排，多阶段过滤保证召回质量

4. **三层记忆 + 异步回写**：分层设计匹配不同生命周期需求，偏好提取有 5s 超时兜底不阻塞用户响应，数组字段追加式更新保证偏好积累

5. **全链路容错降级**：每个节点都有 fallback 方案（LLM 失败 → 默认分类、JSON 解析失败 → 兜底计划、Redis 断连 → 进程内存），保证服务永远有响应

### 4. 面试官可能追问

1. 为什么用 Agent，而不是普通的 LLM + 多轮对话？
2. 为什么要拆成两条路径，不统一用一种方式？
3. ReAct 路径和 Plan-then-Execute 路径各自的优劣是什么？
4. Intent Router 分错了怎么办？有没有纠错机制？
5. Planner 生成的计划如果工具名写错了怎么办？
6. StepExecutor 为什么不加 LLM 做中间判断？
7. 如何防止 ReAct Agent 无限循环调用工具？
8. 为什么 Planner 用 DeepSeek R1 而不是 Qwen3？
9. RAG 为什么用混合检索而不是纯向量？
10. 记忆系统如何保证一致性？Redis 和 PG 数据会不会冲突？
11. 如何评估这个 Agent 系统的效果？
12. 如果要支持 10 倍并发，架构需要怎么改？

### 5. 重点问题参考答案

---

**Q1：为什么要拆成两条路径，不统一用一种方式？**

**考察点**：对 Agent 架构 trade-off 的理解，是否有实际调优经验。

**推荐回答**：

> 核心原因是**响应速度和规划深度的矛盾**。日常出行问题如果走 Plan-then-Execute，用户等 DeepSeek R1 想 2-3 秒再执行工具，体验很差。完整行程规划如果走 ReAct，LLM 自主决策时容易遗漏维度（比如只查了景点没查交通），规划不够系统。
>
> 拆开后：ReAct 路径延迟低（LLM 直接决定下一步），适合 1-2 次工具调用的日常出行场景；Plan-then-Execute 路径虽然多一步规划但保证了完整性（计划阶段就列出所有需要查的维度），适合 4-6 次工具调用的行程规划场景。

**可能继续追问**：那分类错了怎么办？比如用户说了很多信息但被判成 simple_travel？

> 目前靠 prompt 中明确定义分类边界（有出发地+目的地+天数→full_travel）。未来可以加置信度阈值——分类不确定时走 full_travel（宁可多做不漏做），或者加反馈机制让用户确认。

---

**Q2：Plan-then-Execute 中，计划和执行为什么要分成两个节点？**

**考察点**：系统设计解耦思维、可观测性意识。

**推荐回答**：

> 三个原因：
>
> 第一是**可审计性**——计划是 JSON 结构，可以直接存储、回放、人工审核，知道系统"打算做什么"；
>
> 第二是**容错隔离**——计划生成失败可以用兜底计划继续执行，某一步工具调用失败只影响当步不影响后续步骤；
>
> 第三是**模型分工**——计划生成需要推理能力用 DeepSeek R1（低温、高准确性），执行不需要 LLM 只是按计划调工具，降低成本。

---

**Q3：如何防止 ReAct Agent 无限循环？**

**考察点**：对 Agent 风险的认知和防御措施。

**推荐回答**：

> `create_react_agent` 内部有最大迭代次数限制（默认 25 次）。在实践中日常出行查询一般 2-4 轮就结束了。额外的保护包括：整体请求有超时控制，SSE 连接有最大时长。如果需要更精细的控制，可以在 react_executor 里加 step count 监控，超过阈值强制终止并走 summarizer 输出已有结果。

---

**Q4：为什么记忆要分三层？**

**考察点**：分层设计思维、对不同存储特性的理解。

**推荐回答**：

> 三层对应三种生命周期和访问模式：
>
> - **Working Memory**（进程内 dict）：当前请求的中间状态，亚毫秒访问，请求结束即清除；
> - **Short-Term**（Redis）：会话上下文（20轮/30分钟），支持跨请求的多轮对话关联，毫秒级访问；
> - **Long-Term**（PG + ChromaDB）：用户偏好，跨会话持久化，支撑个性化出行建议。
>
> 如果全放 PG 则每次 LLM 调用前都要查数据库，延迟不可接受；如果全放内存则重启丢失。分层让每一层只承担匹配其特性的数据。

---

**Q5：如何评估这个 Agent 系统的效果？**

**考察点**：工程成熟度、是否有质量意识。

**推荐回答**：

> 目前评估分几个维度：
>
> - **意图分类准确率**：用标注数据集测 IntentRouter 的三分类准确率；
> - **RAG 召回率**：有 `eval_rag_recall.py` 评估检索相关性；
> - **端到端质量**：人工评估最终回答的信息完整度、个性化程度、格式规范性。
>
> 未来想做的是自动化评估——用 LLM-as-Judge 对比输出和参考答案，跑 CI 级别的回归测试，防止改一处坏一片。
