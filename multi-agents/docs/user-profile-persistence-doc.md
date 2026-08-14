# 用户画像持久化 技术文档

## 一、架构设计

### 1. 功能背景

旅行规划是一个高度个性化的场景。用户在多次对话中逐渐暴露偏好（预算习惯、旅行风格、饮食禁忌等），如果每次对话都从零开始理解用户，体验非常差。

**核心问题**：如何在多轮、多会话对话中持续积累用户画像，并在后续规划中自动应用？

**解决方案**：设计了一套**三层分布式记忆架构**，通过「记忆提升 Pipeline」在每轮对话结束后自动提取偏好，写入持久化存储（PostgreSQL），下次对话加载时即可个性化推荐。

### 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  工作记忆 (Working Memory)                                    │
│  进程内 dict，零延迟，session 内隔离                             │
│  生命周期：单次请求处理期间                                      │
└─────────────────────────────────────────────────────────────┘
                            ↓ promote
┌─────────────────────────────────────────────────────────────┐
│  短期记忆 (Short-Term Memory)                                 │
│  Redis List，最近 20 轮，TTL 30min                             │
│  Key: smart_travel:short_term:{session_id}                   │
│  Redis 不可用时降级为内存 dict                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓ promote
┌─────────────────────────────────────────────────────────────┐
│  长期记忆 (Long-Term Memory)                                  │
│  PostgreSQL user_preferences 表 + conversation_summaries     │
│  Chroma RAG 向量知识库                                        │
│  生命周期：永久                                                │
└─────────────────────────────────────────────────────────────┘
```

### 3. 记忆系统模块划分

| 模块 | 文件 | 职责 |
|------|------|------|
| MemoryManager | `memory/manager.py` | 全局单例管理器，聚合所有层 |
| WorkingMemory | `memory/working.py` | 进程内 dict，请求级缓存 |
| ShortTermMemory | `memory/short_term.py` | Redis 会话缓存，20 轮滑动窗口 |
| LongTermMemory | `memory/long_term.py` | PG 用户偏好 + 摘要归档 + RAG |
| MemoryRouter | `memory/router.py` | 按意图决定从哪些层加载 |
| MemoryPromotion | `memory/promotion.py` | 请求结束后执行偏好提取和写回 |
| PreferenceExtractor | `memory/preference_extractor.py` | LLM 结构化输出提取隐含偏好 |

### 4. 数据流全链路

```
用户发送消息
    │
    ▼
┌──────────────────────────────────────────┐
│  IntentRouter 节点                        │
│  1. WorkingMemory.get_context()           │
│  2. ShortTermMemory.get_history()         │
│  3. LongTermMemory.get_preferences()      │
│  → 构建 memory_context 注入 State         │
└──────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────┐
│  下游 Agent 执行（ReactExecutor/Planner） │
│  → 偏好信息传递给 LLM 做个性化推荐        │
└──────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────┐
│  Summarizer → FinalOutput 节点            │
│  调用 MemoryPromotion.promote()           │
│  ┌────────────────────────────────────┐  │
│  │ 1. 写入 Redis 短期记忆             │  │
│  │ 2. 正则快路径提取偏好              │  │
│  │ 3. 未命中 → LLM 兜底提取 (5s超时) │  │
│  │ 4. 白名单过滤 + 数组合并去重       │  │
│  │ 5. upsert 写入 PG 长期记忆        │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
    │
    ▼
  下次对话自动加载更新后的画像
```

---

## 二、细节实现

### 1. 核心执行流程：记忆提升 Pipeline

`MemoryPromotion.promote()` 是画像持久化的核心入口，在每次对话完成后触发：

```python
async def promote(self, session_id, user_id, user_message, assistant_response, pg_session_id):
    # Step 1: 写入 Redis 短期记忆
    await self.short_term.add_message(session_id, "user", user_message)
    await self.short_term.add_message(session_id, "assistant", assistant_response)

    # Step 2: 正则快路径提取
    preferences = self._extract_preferences(user_message, assistant_response)

    # Step 3: 未命中 → LLM 兜底（5s 超时）
    if not preferences:
        preferences = await asyncio.wait_for(
            extract_preferences_with_llm(user_message, assistant_response, current_prefs),
            timeout=5.0,
        )

    # Step 4: 写入 PG（白名单过滤 + 数组合并去重）
    if preferences:
        await self.long_term.update_preferences(user_id, **preferences)
```

### 2. 双路径偏好提取

#### 路径一：正则快路径（<10ms，零 LLM 开销）

处理用户显式表达的偏好：

```python
def _extract_preferences(self, user_msg, assistant_msg):
    fields = {}
    # 预算：「预算5000」→ budget_level = "高端型"
    budget_match = re.search(r'预算[大概约是]?(\d+)', user_msg)
    # 喜好：「我喜欢古镇」→ liked_activities = ["古镇"]
    like_match = re.findall(r'(?:我)?喜欢([一-鿿]{2,6})', user_msg)
    # 厌恶：「不想爬山」→ disliked_activities = ["爬山"]
    dislike_match = re.findall(r'(?:我)?(?:不喜欢|讨厌|不想)([一-鿿]{2,6})', user_msg)
    # 美食：「想吃火锅」→ cuisine_preference = ["火锅"]
    cuisine_match = re.findall(r'(?:喜欢吃|想吃|爱吃)([一-鿿]{2,6})', user_msg)
    return fields
```

#### 路径二：LLM 结构化输出（处理隐含语义）

正则无法捕获的隐含偏好由 LLM 提取：

- "带着爸妈" → `travel_style=["家庭游","慢节奏"]`
- "别太赶" → `daily_schedule_preference="悠闲"`
- "对海鲜过敏" → `dietary_restrictions=["海鲜"]`

核心实现：
```python
class PreferenceExtraction(BaseModel):
    """Pydantic schema — 字段与 ALLOWED_PREF_FIELDS 对齐"""
    travel_style: List[str] = Field(default_factory=list)
    budget_level: Optional[str] = None
    has_preference: bool = Field(default=False)
    confidence: float = Field(default=0.0, ge=0, le=1)

async def extract_preferences_with_llm(user_msg, assistant_msg, current_prefs):
    llm = ChatOpenAI(model=QWEN3_MODEL, temperature=0.1)
    structured = llm.with_structured_output(PreferenceExtraction)
    result = await chain.ainvoke(...)
    # 置信度过滤：<0.6 丢弃，防止误提取
    if not result.has_preference or result.confidence < 0.6:
        return {}
    return result.model_dump(exclude={"has_preference", "confidence"})
```

### 3. 数据库持久化策略

#### 白名单防护

```python
ALLOWED_PREF_FIELDS = {
    "travel_style", "budget_level", "hotel_preference",
    "liked_activities", "disliked_activities", "cuisine_preference",
    "transport_priority", "max_daily_budget", "dietary_restrictions",
    "room_type_preference", "destination_types",
    "travel_season_preference", "daily_schedule_preference",
}
```

所有写入操作先过白名单过滤，防止 LLM 输出的非法字段名拼接成 SQL 注入。

#### 数组累积合并（append + 去重）

画像的核心设计思想：**累积而非覆盖**。

```python
ARRAY_PREF_FIELDS = {"travel_style", "liked_activities", "cuisine_preference", ...}

# 合并逻辑
if key in ARRAY_PREF_FIELDS and isinstance(value, list):
    old = existing.get(key) or []
    merged = old + [v for v in value if v not in old]  # append + 去重
    merged_fields[key] = merged
else:
    merged_fields[key] = value  # 标量字段直接覆盖
```

**效果示例**：
- 第1次对话：用户说"喜欢古镇" → `liked_activities = ["古镇"]`
- 第2次对话：用户说"喜欢自然风光" → `liked_activities = ["古镇", "自然风光"]`
- 重复说"古镇"不会产生重复项

#### PostgreSQL 表结构

```sql
CREATE TABLE user_preferences (
    user_id             VARCHAR(64) PRIMARY KEY,
    travel_style        TEXT[] DEFAULT '{}',
    budget_level        VARCHAR(20) DEFAULT '舒适型',
    hotel_preference    TEXT[] DEFAULT '{}',
    liked_activities    TEXT[] DEFAULT '{}',
    disliked_activities TEXT[] DEFAULT '{}',
    cuisine_preference  TEXT[] DEFAULT '{}',
    transport_priority  TEXT[] DEFAULT ARRAY['性价比','时间'],
    max_daily_budget    NUMERIC(10,2),
    dietary_restrictions TEXT[] DEFAULT '{}',
    destination_types   TEXT[] DEFAULT '{}',
    travel_season_preference TEXT[] DEFAULT '{}',
    daily_schedule_preference VARCHAR(20) DEFAULT '随性',
    extra               JSONB DEFAULT '{}',
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);
```

### 4. 记忆路由：智能加载策略

`MemoryRouter.load_context()` 根据意图决定加载哪些层：

```python
async def load_context(self, session_id, user_id, user_query):
    context = {}
    context["working"] = self.working_memory.get_context(session_id)      # 始终加载
    context["short_term"] = await self.short_term.get_history(session_id)  # session 有历史时
    context["preferences"] = await self.long_term.get_preferences(user_id) # 始终加载（PG 单行成本低）
    if self._needs_knowledge(user_query):                                  # 按需加载 RAG
        context["knowledge"] = await self.long_term.search_knowledge(user_query)
    return context
```

### 5. 容错设计

| 场景 | 处理方式 |
|------|----------|
| Redis 不可用 | 自动降级为进程内 dict，不影响主流程 |
| LLM 偏好提取超时 | `asyncio.wait_for(timeout=5.0)` 兜底，超时跳过不阻塞流式输出 |
| LLM 提取异常 | try-except 捕获，打印 warning，不影响对话 |
| 置信度不足 | `confidence < 0.6` 时丢弃结果，宁可漏提不误提 |
| 非法字段名 | 白名单过滤，LLM 输出的非预期字段直接丢弃 |

---

## 三、未来改进方向

### 1. 偏好提取优化

**当前问题**：正则只能匹配固定句式（"我喜欢X"），覆盖面有限；LLM 兜底虽然能提取隐含语义，但有 5s 延迟和 token 成本。

**改进方向**：
- 引入 Few-shot 示例提升 LLM 提取准确率
- 正则规则扩展为可配置的规则引擎（YAML 配置化）
- 批量异步提取：多轮对话攒批后统一提取，降低 LLM 调用次数

**收益**：提取覆盖率从 ~60% 提升到 ~85%，单次 LLM 成本降低 50%。

### 2. 画像衰减与时效性

**当前问题**：偏好只增不减，用户口味变化后旧偏好不会自动失效。

**改进方向**：
- 为每条偏好添加 `confidence` 和 `last_confirmed_at` 字段
- 引入衰减机制：长时间未被确认的偏好权重降低
- 冲突检测：新偏好与旧偏好矛盾时（如"不喜欢古镇"vs"喜欢古镇"），自动更新

**收益**：画像更准确反映用户当前偏好，避免推荐失准。

### 3. 向量化用户画像

**当前问题**：偏好以结构化字段存储，只能精确匹配，无法进行语义级别的相似度计算。

**改进方向**：
- 将用户画像 Embedding 化，存入向量数据库
- 支持"找到与我口味相似的旅行方案"类查询
- 用户画像与 RAG 知识库做交叉检索

**收益**：从规则匹配升级为语义匹配，推荐精准度提升。

### 4. 多用户画像隔离与合并

**当前问题**：一个 user_id 对应一份画像，无法处理"帮我和家人一起规划"的多角色场景。

**改进方向**：
- 支持临时画像叠加（旅伴偏好临时合并）
- 冲突解决策略（取交集 / 加权平均）

**收益**：支持团体出行场景，覆盖更多真实用例。

### 5. Agent 效果评估

**当前问题**：缺乏系统化的评估机制，无法量化画像提取准确率和推荐满意度。

**改进方向**：
- 构建偏好提取 benchmark（标注数据集 + 自动评测）
- 引入 A/B 测试：有画像 vs 无画像的推荐点击率对比
- 用户隐式反馈收集（采纳推荐 = 正反馈，修改推荐 = 负反馈）

**收益**：数据驱动迭代，量化画像系统对推荐质量的贡献。

---

## 四、面试话术

### 1. 30 秒介绍

> 我在智慧出行项目中设计了用户画像持久化系统。核心问题是：旅行规划高度个性化，用户的偏好分散在多轮对话中，需要自动提取并跨会话复用。我采用三层记忆架构（工作记忆 → Redis 短期 → PG 长期），通过「正则快路径 + LLM 兜底」的双路径提取策略，在每次对话结束后自动提取偏好写入 PostgreSQL，下次对话时加载画像做个性化推荐。数组字段采用 append + 去重策略实现累积式画像构建。

### 2. 1~2 分钟完整介绍

> **业务背景**：我们的智慧出行助手是一个多 Agent 旅行规划系统。用户在多次对话中会逐渐暴露偏好——比如第一次说"预算 5000"，第二次说"喜欢古镇"，第三次说"对海鲜过敏"。如果每次对话都从零理解用户，推荐会非常泛化。
>
> **Agent 架构**：我设计了三层记忆架构。工作记忆是进程内 dict，零延迟；短期记忆用 Redis 缓存最近 20 轮对话；长期记忆用 PostgreSQL 持久化用户画像。核心是记忆提升 Pipeline——每次对话结束后自动触发。
>
> **核心实现**：偏好提取采用双路径策略。正则快路径处理显式表达（"预算 5000"、"喜欢古镇"），延迟在毫秒级。正则未命中时启动 LLM 兜底，用 Pydantic 结构化输出提取隐含语义（"带着爸妈"推导出家庭游风格），设 5 秒超时不阻塞流式输出。写入 PG 时做白名单过滤防注入，数组字段 append + 去重实现画像累积。
>
> **技术难点**：一是保证提取精度——LLM 输出加了 confidence 阈值（<0.6 丢弃），宁可漏提不误提；二是不阻塞流式——5 秒超时兜底，超时则跳过本次提取；三是容错——Redis 不可用自动降级内存，全链路异步化。
>
> **最终效果**：用户第二次来对话时，系统自动加载已有画像，推荐直接就能命中偏好，不需要用户重复表达，大幅提升了对话效率和满意度。

### 3. 技术亮点

1. **双路径偏好提取**：正则快路径（<10ms）兜底显式表达 + LLM 结构化输出处理隐含语义，兼顾低延迟和高覆盖率
2. **累积式画像构建**：数组字段 append + 去重，用户画像随对话逐步丰富而不是被覆盖，解决了偏好分散在多轮对话中的问题
3. **非阻塞容错设计**：LLM 提取设 5s 超时、Redis 降级内存、置信度过滤，保证画像提取不影响主对话流式输出
4. **白名单安全防护**：`ALLOWED_PREF_FIELDS` 严格过滤字段名，防止 LLM 输出拼接成 SQL 注入
5. **三层记忆分离**：不同生命周期的数据放在不同层，工作记忆零延迟、Redis 热缓存、PG 冷持久化，读写性能最优

### 4. 面试官可能追问

1. 为什么不直接让 LLM 每次都提取偏好，还要做正则快路径？
2. 数组字段为什么用 append + 去重而不是直接覆盖？
3. 5 秒超时的依据是什么？超时后偏好丢失怎么办？
4. 置信度 0.6 的阈值怎么确定的？
5. 白名单是写死的，新增字段怎么办？
6. Redis 降级内存后，短期记忆跨实例不一致怎么处理？
7. 用户画像只增不减，偏好变化了怎么办？
8. 如何评估画像提取的准确率？
9. 如果用户的偏好前后矛盾（"喜欢古镇"后来又说"不喜欢古镇"），怎么处理？
10. 为什么选 PG 存画像而不是直接用 Redis 或 MongoDB？

### 5. 重点问题参考答案

#### Q1: 为什么不直接让 LLM 每次都提取，还要做正则快路径？

**考察点**：成本意识、延迟敏感性、工程权衡

**推荐回答**：
> 三个原因：一是**延迟**，正则提取 <10ms 而 LLM 需要 1-3 秒，对话是流式输出的，不能让用户等；二是**成本**，每次对话都调 LLM 提取偏好，token 成本随用户量线性增长；三是**确定性**，显式表达（"预算5000"）用正则是 100% 准确的，没必要引入 LLM 的不确定性。LLM 只在正则 miss 时兜底处理隐含语义。

**可能追问**：那为什么不完全用正则？→ 正则无法处理隐含语义（"带着爸妈"→家庭游），覆盖率不够。

#### Q2: 数组字段为什么 append + 去重而不是覆盖？

**考察点**：产品理解、数据一致性

**推荐回答**：
> 用户偏好是逐步暴露的。第一次说"喜欢古镇"，第三次说"喜欢自然风光"。如果覆盖，第三次对话后画像只剩"自然风光"，丢失了之前的信息。append + 去重让画像随时间累积，越来越完整。标量字段（如 budget_level）用覆盖是因为预算是互斥的——只能是经济型或高端型。

**可能追问**：那画像会无限膨胀？→ 目前靠字段类型约束（TEXT[] 不会太长），未来可以加容量上限或衰减机制。

#### Q3: 5 秒超时的依据是什么？

**考察点**：性能调优经验、流式体验

**推荐回答**：
> 5 秒是在流式输出完成之后才触发的，用户已经拿到回答了。选 5 秒是因为：Qwen3 模型平均响应在 2-3 秒，5 秒覆盖了 P99 的情况。超时后偏好不会丢失——下次对话如果用户重复相同偏好，还会再触发提取。本质上这是一个"尽力而为"的后台任务，不能因为它阻塞主流程。

#### Q4: 如何评估画像提取的准确率？

**考察点**：工程闭环、数据驱动思维

**推荐回答**：
> 当前通过单元测试覆盖核心场景（正则匹配、LLM 提取、置信度过滤、数组合并）。未来计划构建标注数据集做 benchmark——收集真实对话 + 人工标注期望提取结果，计算 precision/recall。同时可以通过用户隐式反馈评估效果：如果画像加载后推荐被用户采纳就是正反馈，被修改就是负反馈。
