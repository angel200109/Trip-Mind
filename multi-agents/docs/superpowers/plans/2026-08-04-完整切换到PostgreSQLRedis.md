# 完整切换到 PostgreSQL + Redis 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将项目从"SQLite + 内存 dict"完整切换到 "PostgreSQL + Redis" 架构。当前记忆系统代码已接入 workflow，但 PostgreSQL 连接池从未在生产代码中初始化（`init_db()` 只在测试中调用），对话历史仍使用 SQLite（`chat_history_manager`），Redis 短期记忆依赖容器启动且无验证。

**Architecture:** 生产入口（`server.py` FastAPI lifespan / `app.py` Streamlit）启动时初始化 PostgreSQL 连接池并执行迁移；`conversation_service` 从 `chat_history_manager`（SQLite）切换到 `db.models`（asyncpg CRUD）；会话 ID 从字符串（`default_user_xxx`）适配为 UUID；提供 SQLite → PostgreSQL 数据迁移脚本。

**Tech Stack:** asyncpg, PostgreSQL 15, Redis 7, FastAPI lifespan, docker-compose

## Global Constraints

- 异步优先（所有数据库操作均为 async，与 `db/models.py` 现有风格一致）
- 连接字符串来自环境变量 `DATABASE_URL` / `REDIS_URL`（已配置在 `config/settings.py`）
- 切换后保持向后兼容：SQLite 代码保留但不被生产链路调用
- 会话 ID 迁移为 UUID 后，前端 API 契约不变（`/api/conversations` 仍返回 id/title/groupLabel/messageCount）
- 所有时间戳使用 UTC（`TIMESTAMPTZ`）

---

### Task 1: 生产入口初始化 PostgreSQL 连接池（关键缺口）

**Files:**
- Modify: `multi-agents/server.py`（FastAPI lifespan 添加 init_db/close_db）
- Modify: `multi-agents/app.py`（Streamlit 路径初始化）
- Create: `multi-agents/tests/test_server_lifespan.py`

**Interfaces:**
- Consumes: `db.postgres.init_db() -> None`, `db.postgres.close_db() -> None`
- Produces: 启动时连接池就绪，`db.models` 所有 CRUD 可用

- [ ] **Step 1: server.py 添加 lifespan**

在 `multi-agents/server.py` 添加 FastAPI lifespan：

```python
from contextlib import asynccontextmanager
from db.postgres import init_db, close_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 PostgreSQL 连接池，关闭时释放"""
    try:
        await init_db()
        print("[DB] PostgreSQL 连接池已初始化")
    except Exception as e:
        print(f"[DB] PostgreSQL 初始化失败（降级运行）: {e}")
    yield
    try:
        await close_db()
    except Exception:
        pass


app = FastAPI(title="Smart Travel Multi-Agents API", lifespan=lifespan)
```

- [ ] **Step 2: app.py（Streamlit）初始化**

在 `multi-agents/app.py` 的 `run_multi_agents()` 首次调用前（或模块初始化处）确保连接池就绪：

```python
import asyncio
from db.postgres import init_db

# 启动时初始化（幂等，重复调用无副作用）
asyncio.run(init_db())
```

> 注意：Streamlit 脚本每次交互重跑，需确认 `init_db()` 幂等性（已有 `if _pool is not None: return` 保护）。

- [ ] **Step 3: 写 lifespan 测试**

Create `multi-agents/tests/test_server_lifespan.py`：验证 `init_db()` 后 `db.models.get_preferences` 不抛 `RuntimeError`。

- [ ] **Step 4: 启动容器并验证**

```bash
docker compose up -d
docker ps  # 确认 smart-travel-pg / smart-travel-redis Up
```

然后 `python -c "from db.postgres import init_db; import asyncio; asyncio.run(init_db())"` 确认无异常。

- [ ] **Step 5: Commit**

```bash
git add server.py app.py tests/test_server_lifespan.py
git commit -m "feat(db): initialize PostgreSQL pool in production entry points"
```

---

### Task 2: conversation_service 切换到 PostgreSQL（替换 SQLite）

**Files:**
- Modify: `multi-agents/services/conversation_service.py`
- Modify: `multi-agents/services/chat_service.py`
- Create: `multi-agents/tests/test_conversation_service_pg.py`

**Interfaces:**
- Consumes: `db.models`（create_session/get_user_sessions/save_message/get_session_messages/update_session_title/delete_session）
- Produces: 与现有一致——`list_conversations() -> list[ConversationListItem]`, `get_conversation(id) -> ConversationDetail`, `create_conversation()`, `add_message()`, `delete_conversation()`, `update_title()`

- [ ] **Step 1: 重写 ConversationService 使用 db.models**

`multi-agents/services/conversation_service.py` 内部 `self._manager: ChatHistoryManager` 替换为 `db.models` 异步 CRUD：

- `list_conversations()` → `db.models.get_user_sessions(user_id, limit)` + `compute_group_label(updated_at)`（保留现有分组逻辑）
- `get_conversation(id)` → `db.models.get_session_messages(session_id)`（session_id 转 UUID）
- `create_conversation()` → `db.models.create_session(user_id, title)`
- `add_message()` → `db.models.save_message(session_id, role, content, metadata)`
- `delete_conversation()` → `db.models.delete_session(session_id)`
- `update_title()` → `db.models.update_session_title(session_id, title)`

> 注意 `message_count`：PG 端可用 `COUNT(*)` 子查询或返回后计算，保持 `ConversationListItem.messageCount` 字段契约。

- [ ] **Step 2: 会话 ID 类型适配（UUID vs 字符串）**

SQLite 会话 ID 是字符串（`default_user_xxx`），PG 是 UUID。检查所有调用点：
- `chat_service.py` 中 `conv_service.create_conversation()` / `add_message(conversation_id, ...)`
- `api/conversations.py` 的路径参数
- 前端传回的 `conversationId` 是字符串 → 后端转 UUID 时容错（`uuid.UUID(str)` + try/except）

- [ ] **Step 3: 写服务层测试**

Create `multi-agents/tests/test_conversation_service_pg.py`：验证列表/详情/创建/删除全流程（需要 PG 容器运行）。

- [ ] **Step 4: 运行验证**

```bash
python -m pytest tests/test_conversation_service_pg.py -v
# 手工验证 API
python -c "from fastapi.testclient import TestClient; from server import app; c=TestClient(app); print(c.get('/conversations').json())"
```

- [ ] **Step 5: Commit**

```bash
git add services/conversation_service.py services/chat_service.py tests/test_conversation_service_pg.py
git commit -m "feat(db): switch conversation_service from SQLite to PostgreSQL"
```

---

### Task 3: SQLite → PostgreSQL 数据迁移脚本

**Files:**
- Create: `multi-agents/scripts/migrate_sqlite_to_pg.py`

**Interfaces:**
- Consumes: `chat_history_manager`（读 SQLite）、`db.models`（写 PG）
- Produces: 现有 6 个会话、全部消息迁移到 PG

- [ ] **Step 1: 写迁移脚本**

```python
"""
SQLite → PostgreSQL 数据迁移
用法: python scripts/migrate_sqlite_to_pg.py
"""
import asyncio
import uuid
from chat_history_manager import get_chat_history_manager
from db.postgres import init_db, close_db
from db import models


async def migrate():
    await init_db()
    mgr = get_chat_history_manager()
    sessions = mgr.get_user_sessions(limit=500)

    for s in sessions:
        # 生成确定性 UUID（基于原 session_id 的 hash）保持引用稳定
        new_id = uuid.uuid5(uuid.NAMESPACE_URL, s.session_id)
        # 插入会话（保留 title/created_at/updated_at）
        await models.create_session_with_id(new_id, s.user_id, s.title,
                                            s.created_at, s.updated_at)
        # 迁移消息
        for m in mgr.get_session_messages(s.session_id):
            await models.save_message(new_id, m.message_type, m.content,
                                      metadata=m.metadata)
        print(f"  [OK] {s.session_id} -> {new_id} ({s.message_count}条消息)")

    await close_db()


if __name__ == "__main__":
    asyncio.run(migrate())
```

- [ ] **Step 2: db/models.py 增加 create_session_with_id 辅助**

（可选）在 `db/models.py` 增加保留 id/时间戳的插入函数，供迁移使用。

- [ ] **Step 3: 运行迁移并验证**

```bash
python scripts/migrate_sqlite_to_pg.py
python -c "from db.postgres import init_db, close_db; import asyncio; from db import models; asyncio.run(init_db()); print(asyncio.run(models.get_user_sessions('default_user')))"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_sqlite_to_pg.py db/models.py
git commit -m "feat(db): add SQLite to PostgreSQL migration script"
```

---

### Task 4: Redis 短期记忆真实启用与验证

**Files:**
- Modify: `multi-agents/memory/short_term.py`（如有必要）
- Create: `multi-agents/tests/test_short_term_redis_live.py`

**Interfaces:**
- Consumes: `config.settings.REDIS_URL`
- Produces: 确认 `_redis_available=True`，短期记忆真正落 Redis

- [ ] **Step 1: 启动 Redis 容器并验证连接**

```bash
docker compose up -d redis
python -c "
import asyncio
from memory.short_term import ShortTermMemory
stm = ShortTermMemory()
async def t():
    await stm.add_message('s1', 'user', '测试')
    print('redis_available:', stm._redis_available)
    print(await stm.get_history('s1'))
asyncio.run(t())
"
```

预期：`redis_available: True`，历史从 Redis 读取。若 `False` 则排查容器/端口/URL。

- [ ] **Step 2: 检查降级逻辑边界**

确认 `_get_redis()` 在 Redis 不可用时降级内存、恢复后能重连（而非永久 `_redis_available=False` 锁死）。

- [ ] **Step 3: Commit**

```bash
git add memory/short_term.py tests/test_short_term_redis_live.py
git commit -m "feat(memory): verify and enable Redis short-term memory"
```

---

### Task 5: 前端适配与会话 ID 对接

**Files:**
- Modify: `TripMate/src/store/index.ts`（如必要）
- Modify: `multi-agents/api/conversations.py`（如必要）

**Interfaces:**
- Consumes: 后端 `/api/conversations` 系列接口
- Produces: 前端会话 ID（UUID 字符串）正常流转

- [ ] **Step 1: 验证前端会话 ID 流转**

切换 PG 后 `GET /api/conversations` 返回的 `id` 是 UUID 字符串。前端 store 用字符串 id 作 key、URL `/chat/:id` 传递——确认无类型问题（`string` 类型天然兼容）。

- [ ] **Step 2: 会话切换回归测试**

前端切换会话、刷新恢复（`syncConversationByRoute`）、创建新会话——验证 PG 数据正常读写。

- [ ] **Step 3: 删除 4 个空会话后的列表验证**

确认 `messageCount` 在 PG 侧计算正确（列表显示条数）。

---

### Task 6: 集成回归 + 文档更新

**Files:**
- Modify: `multi-agents/docs/项目启动指南.md`
- Modify: `multi-agents/docs/记忆系统实现报告.md`（更新未完成项）

- [ ] **Step 1: 全量测试**

```bash
docker compose up -d
python -m pytest tests/ -v
```

预期：原有 44 个记忆/DB 测试 + 新增测试全部 PASS。

- [ ] **Step 2: 端到端验证**

1. 前端发一条完整旅行规划（travel 模式）→ 确认最终方案流式输出
2. 前端发"你好"（conversation 模式）→ 确认流式输出
3. 检查 PG：`user_preferences`、`travel_history`、`conversation_summaries` 有新数据
4. 检查 Redis：短期记忆 key `smart_travel:short_term:*` 存在

- [ ] **Step 3: 更新文档**

- `docs/项目启动指南.md`：明确"首次启动需 `docker compose up -d` + 运行迁移脚本"
- `docs/记忆系统实现报告.md`：勾选第 208 行"将 SQLite chat_history_manager 完全迁移到 PG"完成项

- [ ] **Step 4: Commit**

```bash
git add docs/ tests/
git commit -m "docs: update startup guide and memory report for PG/Redis migration"
```

---

### Task 7: 清理 SQLite 遗留

**Files:**
- Delete（可选）: `multi-agents/chat_history_manager.py`（确认无引用后）
- Modify: `multi-agents/.gitignore`

- [ ] **Step 1: 确认无生产引用**

```bash
grep -rn "chat_history_manager" --include="*.py" api/ services/ server.py app.py
```

预期：仅测试引用或零引用。

- [ ] **Step 2: 归档 SQLite**

`.gitignore` 增加 `multi-agents/data/chat_history.db`（如未覆盖），保留数据文件但不再使用。

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: retire SQLite chat history after PG migration"
```
