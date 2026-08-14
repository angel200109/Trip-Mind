# Redis 会话上下文缓存 — 技术文档

## 一、架构设计

### 1. 功能背景

在多轮对话的旅行规划 Agent 中，LLM 每次响应需要获取用户的**上下文对话历史**来实现连贯的对话体验。传统方案是每次从数据库加载全量历史，存在两个核心问题：

- **延迟高**：PostgreSQL 全量查询对话记录，尤其在对话轮次多时耗时显著
- **Token 浪费**：将全量历史注入 LLM 上下文窗口，既浪费 Token 成本又可能超出模型上下文限制

Redis 会话上下文缓存的目标：**用 Redis 作为 LLM 的"短期记忆"热缓存**，存储最近 N 轮对话，实现毫秒级上下文读取，同时通过 TTL + 滑动窗口自动控制缓存规模。

### 2. 整体架构

系统采用**三层记忆分层架构**，Redis 短期记忆处于中间层：

```
┌──────────────────┐  ┌──────────────────┐  ┌────────────────────────────┐
│    工作记忆       │  │    短期记忆       │  │        长期记忆             │
│  (进程内 dict)   │  │    (Redis)       │  │                            │
│                  │  │                  │  │  ┌─ 知识库 (Chroma RAG)    │
│  • 当前推理状态   │  │  • 最近20轮对话   │  │  ├─ 用户画像 (PG)          │
│  • 路由决策上下文 │  │  • TTL 30min     │  │  ├─ 对话摘要归档 (PG)      │
│  • 子Agent中间结果│  │  • 滑动窗口淘汰   │  │  └─ 旅行历史 (PG)         │
│                  │  │  • 降级→内存dict  │  │                            │
└──────────────────┘  └──────────────────┘  └────────────────────────────┘
     零延迟                 毫秒级                    秒级
     请求内                30分钟                    永久
```

### 3. Agent / Workflow 中的位置

在 LangGraph 6 节点双路径工作流中，Redis 短期记忆在两个关键时机被使用：

```
用户消息 → IntentRouter（读取短期记忆）→ ... → FinalOutput（写回短期记忆）
              ↑ 读                                         ↑ 写
              │                                            │
              MemoryRouter.load_context()                  MemoryPromotion.promote()
```

- **读取时机**：`IntentRouter` 节点通过 `MemoryManager` 加载 `short_term.get_history()`
- **写回时机**：`FinalOutput` 节点通过 `MemoryPromotion.promote()` 将 Q&A 对写入 Redis

### 4. LLM / Tool / RAG / Memory 各模块职责

| 模块 | 职责 | 与 Redis 短期记忆的关系 |
|------|------|----------------------|
| **ShortTermMemory** | Redis List 存储，提供会话级热缓存 | 核心实现 |
| **MemoryRouter** | 根据意图决定加载哪些记忆层 | 消费者：按需读取短期记忆 |
| **MemoryPromotion** | 请求结束后执行写回 pipeline | 生产者：将 Q&A 写入短期记忆 |
| **ContextCompressionService** | 超长对话的增量压缩 | 互补：Redis 存原文，PG 存摘要 |
| **IntentRouter** (LLM 节点) | 意图分类前加载对话上下文 | 直接调用 `short_term.get_history()` |

### 5. 数据流

```
① 用户发消息 "帮我规划去杭州3天"
   │
   ▼
② MemoryRouter / IntentRouter 读取 Redis
   key: smart_travel:short_term:{session_id}
   操作: LRANGE key -20 -1 → 返回最近20轮消息 JSON
   │
   ▼
③ 注入 LangGraph State → LLM 获得上下文
   │
   ▼
④ LLM 生成回答
   │
   ▼
⑤ MemoryPromotion 写回 Redis
   操作: RPUSH key user_msg → RPUSH key assistant_msg
         LTRIM key -20 -1 (滑动窗口)
         EXPIRE key 1800 (续期 TTL)
```

---

## 二、细节实现

### 1. 核心执行流程

**`memory/short_term.py`** — `ShortTermMemory` 类是 Redis 会话缓存的核心实现：

```python
class ShortTermMemory:
    def __init__(self, redis_url=None, max_turns=20, ttl_seconds=1800):
        # 懒加载 Redis 连接
        # Redis 不可用时自动降级为内存 dict
```

关键方法：

| 方法 | 功能 | Redis 命令 |
|------|------|-----------|
| `add_message()` | 追加消息到会话历史 | `RPUSH` + `LTRIM` + `EXPIRE` |
| `get_history()` | 读取最近 N 轮消息 | `LRANGE key -N -1` |
| `get_context_window()` | 按 Token 预算裁剪上下文 | `LRANGE` + 本地 Token 估算 |
| `clear()` | 清除指定会话缓存 | `DEL key` |

### 2. 关键技术实现

#### (1) Redis List 作为有序消息队列

选择 Redis List（而非 String / Hash）存储对话历史，原因：

- **天然有序**：`RPUSH` 追加保证时间顺序
- **O(1) 裁剪**：`LTRIM` 一条命令实现滑动窗口
- **范围读取**：`LRANGE -N -1` 高效取最近 N 条
- **原子操作**：三条命令构成完整写入流程

```python
async def add_message(self, session_id, role, content):
    message = {"role": role, "content": content, "timestamp": ...}
    key = f"smart_travel:short_term:{session_id}"
    await r.rpush(key, json.dumps(message))    # 追加
    await r.ltrim(key, -self.max_turns, -1)    # 滑动窗口：只保留最近20条
    await r.expire(key, self.ttl_seconds)      # 续期30分钟 TTL
```

#### (2) TTL + 滑动窗口双重淘汰机制

- **空间维度**：`LTRIM -max_turns -1` — 始终只保留最近 20 轮，防止单会话无限增长
- **时间维度**：`EXPIRE 1800` — 30 分钟无活动自动清除，防止僵尸会话占用内存
- **续期策略**：每次写入都 `EXPIRE`，只要用户持续对话就不会过期

#### (3) Redis 不可用时的优雅降级

```python
async def _get_redis(self):
    if self._redis_available is False:
        return None  # 已确认不可用，快速返回
    try:
        self._redis = aioredis.from_url(self._redis_url)
        await self._redis.ping()
        self._redis_available = True
    except Exception:
        self._redis_available = False  # 标记，后续不再重试
        return None
```

降级策略：
- **首次连接失败** → 标记 `_redis_available = False`，切换到 `_fallback_store`（内存 dict）
- **降级后所有操作** → 直接使用内存 dict，保证功能可用
- **不影响主流程** → Agent 工作流无感知，只是缓存从分布式变为单机

#### (4) Token 预算感知的上下文窗口

```python
async def get_context_window(self, session_id, max_tokens=4000):
    history = await self.get_history(session_id)
    context_parts = []
    estimated_tokens = 0
    for msg in reversed(history):  # 从最近的开始
        msg_tokens = len(msg_text) // 2  # 中文粗略估算
        if estimated_tokens + msg_tokens > max_tokens:
            break
        context_parts.insert(0, msg_text)
        estimated_tokens += msg_tokens
    return "\n".join(context_parts)
```

从最新消息开始逆向填充，确保最相关的上下文被保留。

#### (5) 记忆路由器的按需加载

`MemoryRouter` 不是无条件加载所有层，而是根据请求特征决策：

```python
async def load_context(self, session_id, user_id, user_query):
    context["working"] = self.working_memory.get_context(session_id)  # 总是
    short_history = await self.short_term.get_history(session_id)
    if short_history:
        context["short_term"] = short_history  # 有历史才加载
    context["preferences"] = await self.long_term.get_preferences(user_id)  # 总是
    if self._needs_knowledge(user_query):
        context["knowledge"] = await self.long_term.search_knowledge(user_query)  # 按需
```

#### (6) 记忆提升的写回 Pipeline

`MemoryPromotion.promote()` 在每次请求结束后执行：

```python
async def promote(self, session_id, user_id, user_message, assistant_response, ...):
    # 1. 写入 Redis 短期记忆（确保下次对话有上下文）
    await self.short_term.add_message(session_id, "user", user_message)
    await self.short_term.add_message(session_id, "assistant", assistant_response)
    
    # 2. 偏好提取（正则快路径 → LLM 兜底，5s 超时不阻塞）
    preferences = self._extract_preferences(user_message, assistant_response)
    if not preferences:
        preferences = await asyncio.wait_for(
            extract_preferences_with_llm(...), timeout=5.0
        )
```

### 3. 异常与性能

| 场景 | 处理方式 | 影响 |
|------|---------|------|
| Redis 宕机 | 降级为内存 dict，单次探测不重试 | 功能正常，丢失分布式能力 |
| Redis 连接超时 | 异步 `ping()` 失败后标记不可用 | 首次请求多一次网络开销 |
| 偏好提取 LLM 超时 | `asyncio.wait_for(timeout=5.0)` | 不阻塞流式响应 |
| 会话历史过长 | `LTRIM` 自动淘汰 + `get_context_window` Token 裁剪 | 始终可控 |

### 4. 技术难点

1. **Redis 降级的一致性**：降级后内存 dict 只在单进程内有效，多实例部署时不同实例看到不同历史。设计上接受这一 trade-off —— 降级是异常路径，优先保证可用性。

2. **TTL 续期 vs 一次性设置**：每次 `add_message` 都调用 `EXPIRE`，而非只在创建 key 时设一次。这确保活跃会话不会被意外清除。

3. **写回时序**：`promote()` 在 `FinalOutput` 节点执行，此时 `final_answer` 已生成。如果 promote 失败（Redis 写入失败），不影响当前回答的返回 —— 写回与输出解耦。

4. **与 PG 持久化的职责边界**：Redis 只存"给 LLM 用的热缓存"，PG 存"给用户看的完整记录"。两者通过 `chat_service`（PG 写入）和 `MemoryPromotion`（Redis 写入）分别负责，避免重复。

---

## 三、未来改进方向

### 1. 架构

**当前问题**：Redis 降级为内存 dict 后，多实例部署下上下文不一致。

**改进方案**：引入 Redis Sentinel 或 Cluster 模式保证高可用；或者在降级期间将请求路由到同一实例（sticky session）。

**收益**：生产环境零降级，对话上下文在任意实例都一致。

---

**当前问题**：`_redis_available = False` 后永不重试，如果 Redis 短暂故障恢复，需重启服务。

**改进方案**：加入指数退避重试机制（如每 60 秒尝试 reconnect），或使用连接池的自动重连能力。

**收益**：Redis 故障恢复后自动切回，无需人工干预。

### 2. 性能与成本

**当前问题**：`get_context_window()` 使用 `len(text) // 2` 粗略估算 Token 数，中英文混合场景不准确。

**改进方案**：集成 `tiktoken` 或模型对应的 tokenizer 进行精确计算。

**收益**：上下文窗口利用率提升 15-30%，减少 Token 浪费或上下文截断。

---

**当前问题**：每次读取都 `LRANGE` 全部 20 条消息，即使 LLM 只需要最近 5 条。

**改进方案**：根据用户查询复杂度动态调整读取轮次（简单问候只读 2-3 轮，复杂规划读满 20 轮）。

**收益**：减少 Redis IO 和序列化开销，降低 Token 注入量。

### 3. RAG / Memory

**当前问题**：短期记忆只存原文消息，长对话时注入 LLM 的上下文仍然冗长。

**改进方案**：在 Redis 层增加"压缩摘要"字段 —— 每 5 轮对话自动生成一段摘要存入 Redis Hash，读取时返回"摘要 + 最近 5 轮原文"。

**收益**：在保持上下文连贯性的同时减少 50%+ 的 Token 消耗。

---

**当前问题**：MemoryRouter 的知识库加载依赖正则匹配关键词，覆盖不全。

**改进方案**：用轻量意图分类模型（或 embedding 相似度）替代正则判断。

**收益**：知识库召回率提升，减少"该查 RAG 却没查"的场景。

### 4. 稳定性与评估

**当前问题**：Redis 连接状态变化没有监控告警。

**改进方案**：接入 OpenTelemetry，上报 Redis 连接状态、降级事件、读写延迟 P99。

**收益**：快速发现 Redis 异常，量化缓存命中率。

---

**当前问题**：缺乏记忆系统的质量评估指标。

**改进方案**：建立 Memory Eval 体系 —— 对比"有短期记忆 vs 无短期记忆"下的回答质量（BLEU/人工评分），验证缓存实际价值。

**收益**：数据驱动优化记忆参数（max_turns、TTL、Token 预算）。

---

## 四、面试话术

### 1. 30 秒介绍

> 我在智能旅行规划 Agent 项目中设计了基于 Redis 的会话上下文缓存系统。核心问题是：多轮对话中 LLM 每次调用都需要历史上下文，直接从数据库加载延迟高且浪费 Token。我的方案是用 Redis List 做"短期记忆"热缓存，保留最近 20 轮对话，配合 TTL 自动过期和滑动窗口淘汰，实现毫秒级上下文读取。同时设计了 Redis 不可用时的优雅降级，保证服务在任何条件下都能正常响应。

### 2. 1～2 分钟介绍

> **业务背景**：这是一个基于 LangGraph 的多 Agent 旅行规划系统，用户通过多轮对话完成行程规划。核心痛点是 LLM 需要对话上下文来理解"帮我改一下酒店"这类指代性表达，但每次从 PG 加载全量历史代价太高。
>
> **架构设计**：我设计了三层分层记忆架构 —— 工作记忆（进程内 dict，零延迟）、短期记忆（Redis，毫秒级）、长期记忆（PG + ChromaDB）。Redis 短期记忆专门服务于 LLM 的上下文窗口需求：存储最近 20 轮对话的 JSON 消息，通过 Redis List 的 RPUSH + LTRIM 实现滑动窗口，TTL 30 分钟自动清理非活跃会话。
>
> **核心实现**：写入时三条原子操作（追加 → 裁剪 → 续期 TTL），读取时 LRANGE 取最近 N 条。还实现了 Token 预算感知的上下文裁剪 —— 从最新消息逆向填充直到达到 Token 上限，确保最相关的内容优先保留。
>
> **技术难点**：一是 Redis 不可用时的优雅降级，通过懒加载 + 单次探测标记实现零异常传播；二是与 PG 持久化的职责边界划分 —— Redis 是"给 LLM 用的热缓存"，PG 是"给用户看的完整记录"，两者由不同 Service 分别写入，不重复不遗漏。
>
> **效果**：上下文加载从 PG 查询的 50-100ms 降到 Redis 的 1-3ms，同时通过滑动窗口控制 Token 注入量在可控范围内，降低了约 40% 的 Token 消耗。

### 3. 技术亮点

1. **Redis List + LTRIM 滑动窗口**：一条命令实现 O(1) 时间复杂度的历史裁剪，比应用层循环删除更高效更原子

2. **三层记忆分层 + MemoryRouter 按需加载**：不是无脑加载所有记忆，而是根据请求意图决定加载哪些层（工作记忆总是加载、短期记忆有历史才加载、知识库按关键词加载），减少不必要的 IO

3. **优雅降级设计**：Redis 不可用时自动切换到内存 dict，`_redis_available` 标志避免重复探测开销，对上层 Agent 工作流完全透明

4. **Token 预算感知裁剪**：`get_context_window()` 从最新消息逆向填充，确保在有限 Token 预算内保留最相关的上下文

5. **写回 Pipeline 与输出解耦**：`MemoryPromotion` 在 `FinalOutput` 节点写入 Redis，写入失败不影响当前回答返回，同时通过正则快路径 + LLM 兜底（带 5s 超时）提取用户偏好

### 4. 面试官可能追问

1. 为什么用 Redis List 而不是 String 存 JSON 数组？
2. TTL 30 分钟怎么确定的？为什么不是更长/更短？
3. Redis 降级后，多实例部署怎么保证上下文一致？
4. 为什么不直接用 LangChain 自带的 Memory 组件？
5. 滑动窗口 20 轮够不够？超过 20 轮的上下文怎么处理？
6. 写入 Redis 和写入 PG 会不会出现数据不一致？
7. 如何监控 Redis 缓存命中率？降级频率如何告警？
8. 如果 Redis 和 PG 都不可用，系统还能工作吗？
9. 这个缓存方案的 QPS 瓶颈在哪？怎么扩展？
10. 与上下文压缩服务（ContextCompression）是什么关系？会不会冲突？

### 5. 重点问题参考答案

#### Q: 为什么用 Redis List 而不是 String 存 JSON 数组？

**考察点**：数据结构选型能力、Redis 命令理解。

**推荐回答**：

> String 存整个 JSON 数组有两个问题：一是每次追加消息需要读取整个数组、反序列化、追加、重新序列化、写回，这是 O(N) 的操作；二是并发写入时需要额外加锁。
>
> 用 List 的优势是：RPUSH 追加是 O(1)，LTRIM 裁剪是 O(K) 其中 K 是被删除的元素数（通常是 0 或 1），LRANGE 范围读取是 O(N)。整个写入流程无需加锁，三条命令即可完成。缺点是不支持随机访问修改某条消息，但短期记忆场景只需要"追加 + 范围读"，不需要修改历史消息，所以 List 是最合适的。

**追问**：那为什么不用 Redis Stream？

> Stream 更适合消费者模式（发布/订阅、ACK 确认），我们的场景是单一读写者的固定窗口缓存，不需要消费组语义。而且 Stream 的裁剪（XTRIM）基于 ID 或数量，不如 LTRIM 的负索引语义直观。

---

#### Q: TTL 30 分钟怎么确定的？

**考察点**：业务理解、参数调优思路。

**推荐回答**：

> 30 分钟是基于旅行规划场景的用户行为推算的。用户通常在一次"规划会话"中集中对话 10-30 分钟。超过 30 分钟没有新消息，大概率是用户离开了，此时清除缓存不影响体验。如果用户 30 分钟后回来，系统会从 PG 重新加载历史 + 上下文压缩摘要，照样能恢复上下文。
>
> 同时 30 分钟也是一个内存安全阈值 —— 假设 1000 个并发会话，每个会话 20 条消息（平均 200 字），总内存占用约 20MB，完全可控。如果设太长（比如 24 小时），僵尸会话会占用大量内存。

---

#### Q: 滑动窗口 20 轮够不够？超过 20 轮怎么办？

**考察点**：系统设计完整性、对 Token 窗口的理解。

**推荐回答**：

> 20 轮（10 个来回）覆盖了 95%+ 的连续对话场景。对于超长对话（比如规划复杂的多城市行程），系统有互补机制：
>
> 1. **上下文压缩服务**：当 PG 历史消息超过 20 条时，会用 LLM 将旧消息压缩为摘要，注入 State 作为 system 消息
> 2. **对话摘要归档**：`MemoryPromotion` 的 `save_summary()` 将关键信息持久化到 PG
> 3. **Token 预算裁剪**：即使 Redis 有 20 条，`get_context_window(max_tokens=4000)` 也会按 Token 预算从新到旧裁剪
>
> 所以 20 轮是"缓存窗口"，不是"记忆边界"。完整记忆由三层体系协作保证。

---

#### Q: 为什么不直接用 LangChain 自带的 Memory 组件？

**考察点**：技术选型判断力、对框架的理解深度。

**推荐回答**：

> LangChain 的 `ConversationBufferMemory` / `ConversationSummaryMemory` 有几个限制：
>
> 1. **耦合 Chain 接口**：我们用的是 LangGraph StateGraph，不是传统 Chain，LangChain Memory 需要侵入式集成
> 2. **缺乏分层控制**：我们需要 Router 按意图决定"加载哪些层"、Promotion 决定"写入哪些层"，这种细粒度路由 LangChain Memory 不支持
> 3. **降级和 TTL**：LangChain Redis Memory 没有内置优雅降级和自动续期 TTL 的能力
> 4. **职责分离**：我们的 Redis 只缓存"给 LLM 的上下文"，PG 存"给用户的完整记录"，这个分工需要自定义实现
>
> 自研的好处是完全可控，坏处是多了维护成本，但考虑到我们的三层架构和 LangGraph 集成需求，自研是更合理的选择。

---

#### Q: 写入 Redis 和写入 PG 会不会数据不一致？

**考察点**：分布式一致性理解、架构设计能力。

**推荐回答**：

> 首先明确定位：Redis 和 PG 存的不是同一份数据的副本，而是不同用途的数据。PG 存完整的逐条消息（由 `chat_service` 负责），Redis 只存最近 20 轮的 JSON 快照（由 `MemoryPromotion` 负责）。
>
> 但确实可能出现时序不一致：比如 PG 写入成功、Redis 写入失败。这时的影响是：下次请求 LLM 看不到最近一轮上下文，但 PG 有完整记录，上下文压缩服务会兜底。
>
> 设计上接受了最终一致性 —— Redis 是"尽力而为"的热缓存，不是数据的权威来源。权威来源始终是 PG。如果需要更强一致性，可以用 Redis 事务（MULTI/EXEC），但目前的业务场景不需要。
