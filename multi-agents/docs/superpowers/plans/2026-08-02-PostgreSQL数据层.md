# PostgreSQL 数据库层实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建 PostgreSQL 异步数据库层（`db/postgres.py` + `db/models.py` + 迁移脚本），替代现有 SQLite 的 `chat_history_manager.py`，并扩展支持 `user_preferences`、`conversation_summaries`、`travel_history` 三张新表。

**Architecture:** 使用 `asyncpg` 直连 PostgreSQL，通过连接池管理连接。`db/postgres.py` 负责连接池生命周期，`db/models.py` 提供按表分组的异步 CRUD 函数。迁移脚本使用纯 SQL 文件，由 `db/postgres.py` 在初始化时自动执行。

**Tech Stack:** asyncpg, PostgreSQL 15+, python-dotenv (已有)

## Global Constraints

- Python 3.12+
- 异步优先（所有数据库操作均为 async）
- 连接字符串通过环境变量 `DATABASE_URL` 配置
- 无 ORM — 直接使用 asyncpg 原生查询（保持轻量，与项目现有风格一致）
- UUID 使用 PostgreSQL 的 `gen_random_uuid()`，Python 侧使用 `uuid.uuid4()`
- 所有时间戳使用 UTC（`TIMESTAMP WITH TIME ZONE`）

---

### Task 1: 数据库迁移脚本 + 连接池管理

**Files:**
- Create: `multi-agents/db/__init__.py`
- Create: `multi-agents/db/postgres.py`
- Create: `multi-agents/db/migrations/001_init.sql`
- Create: `multi-agents/tests/test_db_postgres.py`
- Modify: `multi-agents/config/settings.py` (添加 DATABASE_URL)
- Modify: `multi-agents/.env` (添加 DATABASE_URL)
- Modify: `multi-agents/requirements.txt` (添加 asyncpg)

**Interfaces:**
- Consumes: `config/settings.py` 中的 `DATABASE_URL`
- Produces:
  - `db.postgres.get_pool() -> asyncpg.Pool`
  - `db.postgres.init_db() -> None` (创建连接池 + 执行迁移)
  - `db.postgres.close_db() -> None` (关闭连接池)

- [ ] **Step 1: 写迁移脚本**

Create `multi-agents/db/migrations/001_init.sql`:

```sql
-- 001_init.sql
-- 初始化所有表结构

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. 会话表
CREATE TABLE IF NOT EXISTS chat_sessions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       VARCHAR(64) NOT NULL,
    title         VARCHAR(200),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON chat_sessions(user_id, updated_at DESC);

-- 2. 消息表
CREATE TABLE IF NOT EXISTS chat_messages (
    id            BIGSERIAL PRIMARY KEY,
    session_id    UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role          VARCHAR(20) NOT NULL,
    content       TEXT NOT NULL,
    metadata      JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id, created_at);

-- 3. 用户偏好表
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id             VARCHAR(64) PRIMARY KEY,
    travel_style        TEXT[] DEFAULT '{}',
    budget_level        VARCHAR(20) DEFAULT '舒适型',
    hotel_preference    TEXT[] DEFAULT '{}',
    liked_activities    TEXT[] DEFAULT '{}',
    disliked_activities TEXT[] DEFAULT '{}',
    cuisine_preference  TEXT[] DEFAULT '{}',
    transport_priority  TEXT[] DEFAULT ARRAY['性价比','时间'],
    extra               JSONB DEFAULT '{}',
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- 4. 对话摘要归档
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id            SERIAL PRIMARY KEY,
    user_id       VARCHAR(64) NOT NULL,
    session_id    UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    summary       TEXT NOT NULL,
    key_points    TEXT[],
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_summaries_user ON conversation_summaries(user_id, created_at DESC);

-- 5. 旅行历史
CREATE TABLE IF NOT EXISTS travel_history (
    id            SERIAL PRIMARY KEY,
    user_id       VARCHAR(64) NOT NULL,
    session_id    UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    destination   VARCHAR(100),
    origin        VARCHAR(100),
    travel_date   DATE,
    travel_days   INT,
    budget        NUMERIC(10,2),
    plan_summary  TEXT,
    status        VARCHAR(20) DEFAULT 'planned',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_history_user ON travel_history(user_id, created_at DESC);

-- 迁移版本记录表
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INT PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT NOW()
);
```

- [ ] **Step 2: 添加配置和依赖**

Append to `multi-agents/config/settings.py`:

```python
# PostgreSQL 配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/smart_travel")
```

Append to `multi-agents/.env`:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/smart_travel
```

Append to `multi-agents/requirements.txt`:

```
# PostgreSQL 异步驱动
asyncpg>=0.29.0
```

- [ ] **Step 3: 写连接池管理的测试**

Create `multi-agents/tests/test_db_postgres.py`:

```python
"""
测试 db/postgres.py 连接池管理
需要本地 PostgreSQL 运行在 localhost:5432
数据库: smart_travel (需提前创建)
"""
import pytest
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_init_and_close():
    """测试连接池初始化和关闭"""
    from db.postgres import init_db, close_db, get_pool

    await init_db()
    pool = get_pool()
    assert pool is not None

    # 能执行简单查询
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
        assert result == 1

    await close_db()


@pytest.mark.asyncio
async def test_tables_created():
    """测试迁移脚本创建了所有表"""
    from db.postgres import init_db, close_db, get_pool

    await init_db()
    pool = get_pool()

    async with pool.acquire() as conn:
        tables = await conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        table_names = [row["table_name"] for row in tables]

        assert "chat_sessions" in table_names
        assert "chat_messages" in table_names
        assert "user_preferences" in table_names
        assert "conversation_summaries" in table_names
        assert "travel_history" in table_names
        assert "schema_migrations" in table_names

    await close_db()


@pytest.mark.asyncio
async def test_migration_idempotent():
    """测试迁移脚本可重复执行"""
    from db.postgres import init_db, close_db

    await init_db()
    # 再次调用不应报错
    await init_db()
    await close_db()
```

- [ ] **Step 4: 运行测试验证失败**

Run: `cd E:\my\smart-travel\multi-agents && python -m pytest tests/test_db_postgres.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 5: 实现连接池管理**

Create `multi-agents/db/__init__.py`:

```python
"""Database layer — PostgreSQL async connection pool + CRUD models."""
from .postgres import init_db, close_db, get_pool

__all__ = ["init_db", "close_db", "get_pool"]
```

Create `multi-agents/db/postgres.py`:

```python
"""
PostgreSQL 连接池管理
- asyncpg 连接池
- 自动执行迁移脚本
- 全局单例
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import asyncpg

from config.settings import DATABASE_URL

_pool: Optional[asyncpg.Pool] = None

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_pool() -> asyncpg.Pool:
    """获取全局连接池（必须先调用 init_db）"""
    if _pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _pool


async def init_db() -> None:
    """初始化连接池并执行迁移"""
    global _pool
    if _pool is not None:
        return

    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )

    await _run_migrations()


async def close_db() -> None:
    """关闭连接池"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _run_migrations() -> None:
    """按版本号顺序执行未应用的迁移脚本"""
    pool = get_pool()

    async with pool.acquire() as conn:
        # 确保迁移记录表存在
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INT PRIMARY KEY,
                applied_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # 获取已应用的版本
        applied = set()
        rows = await conn.fetch("SELECT version FROM schema_migrations")
        for row in rows:
            applied.add(row["version"])

        # 扫描迁移文件并按版本排序
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

        for file_path in migration_files:
            version = int(file_path.stem.split("_")[0])
            if version in applied:
                continue

            sql = file_path.read_text(encoding="utf-8")
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1)", version
            )
            print(f"[DB] Applied migration: {file_path.name}")
```

- [ ] **Step 6: 安装依赖并确保 PostgreSQL 可用**

Run:
```bash
cd E:\my\smart-travel\multi-agents
pip install asyncpg pytest-asyncio
```

Ensure PostgreSQL is running locally and create the database:
```bash
psql -U postgres -c "CREATE DATABASE smart_travel;" 2>/dev/null || echo "DB already exists"
```

- [ ] **Step 7: 运行测试验证通过**

Run: `cd E:\my\smart-travel\multi-agents && python -m pytest tests/test_db_postgres.py -v`
Expected: 3 tests PASS

- [ ] **Step 8: Commit**

```bash
cd E:\my\smart-travel\multi-agents
git add db/ tests/test_db_postgres.py config/settings.py .env requirements.txt
git commit -m "feat(db): add PostgreSQL connection pool and migration system"
```

---

### Task 2: CRUD Models — chat_sessions + chat_messages

**Files:**
- Create: `multi-agents/db/models.py`
- Create: `multi-agents/tests/test_db_models.py`

**Interfaces:**
- Consumes: `db.postgres.get_pool() -> asyncpg.Pool`
- Produces:
  - `db.models.create_session(user_id, title?) -> uuid.UUID`
  - `db.models.get_user_sessions(user_id, limit?) -> list[dict]`
  - `db.models.save_message(session_id, role, content, metadata?) -> int`
  - `db.models.get_session_messages(session_id, limit?) -> list[dict]`
  - `db.models.update_session_title(session_id, title) -> None`
  - `db.models.delete_session(session_id) -> None`

- [ ] **Step 1: 写 CRUD 测试**

Create `multi-agents/tests/test_db_models.py`:

```python
"""测试 db/models.py 的 CRUD 操作"""
import pytest
import asyncio
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
async def setup_db():
    """每个测试前初始化DB，测试后清理"""
    from db.postgres import init_db, close_db, get_pool

    await init_db()
    yield
    # 清理测试数据
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM chat_messages")
        await conn.execute("DELETE FROM conversation_summaries")
        await conn.execute("DELETE FROM travel_history")
        await conn.execute("DELETE FROM chat_sessions")
        await conn.execute("DELETE FROM user_preferences")
    await close_db()


@pytest.mark.asyncio
async def test_create_session():
    """测试创建会话"""
    from db.models import create_session

    session_id = await create_session("test_user", "测试会话")
    assert isinstance(session_id, uuid.UUID)


@pytest.mark.asyncio
async def test_get_user_sessions():
    """测试获取用户会话列表"""
    from db.models import create_session, get_user_sessions

    await create_session("test_user", "会话1")
    await create_session("test_user", "会话2")
    await create_session("other_user", "其他会话")

    sessions = await get_user_sessions("test_user")
    assert len(sessions) == 2
    assert sessions[0]["title"] in ("会话1", "会话2")


@pytest.mark.asyncio
async def test_save_and_get_messages():
    """测试保存和读取消息"""
    from db.models import create_session, save_message, get_session_messages

    session_id = await create_session("test_user", "聊天")

    msg_id_1 = await save_message(session_id, "user", "你好")
    msg_id_2 = await save_message(session_id, "assistant", "你好！有什么可以帮你？")
    msg_id_3 = await save_message(
        session_id, "assistant", "正在查询...",
        metadata={"tool": "train_query", "params": {"from": "上海"}}
    )

    assert msg_id_1 > 0
    assert msg_id_2 > msg_id_1

    messages = await get_session_messages(session_id)
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"
    assert messages[2]["metadata"]["tool"] == "train_query"


@pytest.mark.asyncio
async def test_update_session_title():
    """测试更新会话标题"""
    from db.models import create_session, update_session_title, get_user_sessions

    session_id = await create_session("test_user", "旧标题")
    await update_session_title(session_id, "杭州3日游规划")

    sessions = await get_user_sessions("test_user")
    assert sessions[0]["title"] == "杭州3日游规划"


@pytest.mark.asyncio
async def test_delete_session_cascades():
    """测试删除会话时级联删除消息"""
    from db.models import (
        create_session, save_message, get_session_messages, delete_session
    )

    session_id = await create_session("test_user", "待删除")
    await save_message(session_id, "user", "test")
    await save_message(session_id, "assistant", "reply")

    await delete_session(session_id)

    messages = await get_session_messages(session_id)
    assert len(messages) == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd E:\my\smart-travel\multi-agents && python -m pytest tests/test_db_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.models'`

- [ ] **Step 3: 实现 models.py**

Create `multi-agents/db/models.py`:

```python
"""
数据库 CRUD 操作
按表分组，所有函数均为 async
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .postgres import get_pool


# ============================================================
# chat_sessions
# ============================================================

async def create_session(user_id: str, title: Optional[str] = None) -> uuid.UUID:
    """创建新会话，返回 session_id"""
    pool = get_pool()
    if not title:
        title = f"对话 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO chat_sessions (user_id, title)
            VALUES ($1, $2)
            RETURNING id
            """,
            user_id, title,
        )
        return row["id"]


async def get_user_sessions(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """获取用户的会话列表，按更新时间倒序"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, title, created_at, updated_at
            FROM chat_sessions
            WHERE user_id = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            user_id, limit,
        )
        return [dict(row) for row in rows]


async def update_session_title(session_id: uuid.UUID, title: str) -> None:
    """更新会话标题"""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE chat_sessions
            SET title = $1, updated_at = NOW()
            WHERE id = $2
            """,
            title, session_id,
        )


async def delete_session(session_id: uuid.UUID) -> None:
    """删除会话（消息通过 ON DELETE CASCADE 自动清除）"""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM chat_sessions WHERE id = $1", session_id
        )


# ============================================================
# chat_messages
# ============================================================

async def save_message(
    session_id: uuid.UUID,
    role: str,
    content: str,
    metadata: Optional[dict] = None,
) -> int:
    """保存一条消息，返回消息 ID"""
    pool = get_pool()
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

    async with pool.acquire() as conn:
        # 插入消息
        row = await conn.fetchrow(
            """
            INSERT INTO chat_messages (session_id, role, content, metadata)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id
            """,
            session_id, role, content, metadata_json,
        )

        # 更新会话的 updated_at
        await conn.execute(
            "UPDATE chat_sessions SET updated_at = NOW() WHERE id = $1",
            session_id,
        )

        return row["id"]


async def get_session_messages(
    session_id: uuid.UUID, limit: int = 200
) -> list[dict[str, Any]]:
    """获取会话的消息列表，按时间正序"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, session_id, role, content, metadata, created_at
            FROM chat_messages
            WHERE session_id = $1
            ORDER BY created_at ASC
            LIMIT $2
            """,
            session_id, limit,
        )

        results = []
        for row in rows:
            item = dict(row)
            if item["metadata"] is not None:
                item["metadata"] = json.loads(item["metadata"])
            results.append(item)
        return results


# ============================================================
# user_preferences
# ============================================================

async def get_preferences(user_id: str) -> Optional[dict[str, Any]]:
    """获取用户偏好，不存在返回 None"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_preferences WHERE user_id = $1", user_id
        )
        return dict(row) if row else None


async def upsert_preferences(user_id: str, **fields) -> None:
    """创建或更新用户偏好（仅更新传入的字段）"""
    pool = get_pool()
    if not fields:
        return

    # 构建 SET 子句
    set_parts = []
    values = [user_id]
    idx = 2

    for key, value in fields.items():
        set_parts.append(f"{key} = ${idx}")
        values.append(value)
        idx += 1

    set_clause = ", ".join(set_parts)

    async with pool.acquire() as conn:
        # 尝试更新
        result = await conn.execute(
            f"UPDATE user_preferences SET {set_clause}, updated_at = NOW() WHERE user_id = $1",
            *values,
        )

        # 如果没有更新到行，则插入
        if result == "UPDATE 0":
            await conn.execute(
                "INSERT INTO user_preferences (user_id) VALUES ($1)", user_id
            )
            await conn.execute(
                f"UPDATE user_preferences SET {set_clause}, updated_at = NOW() WHERE user_id = $1",
                *values,
            )


# ============================================================
# conversation_summaries
# ============================================================

async def save_summary(
    user_id: str,
    session_id: uuid.UUID,
    summary: str,
    key_points: Optional[list[str]] = None,
) -> int:
    """保存对话摘要"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO conversation_summaries (user_id, session_id, summary, key_points)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            user_id, session_id, summary, key_points or [],
        )
        return row["id"]


async def get_user_summaries(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """获取用户的对话摘要列表"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, session_id, summary, key_points, created_at
            FROM conversation_summaries
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id, limit,
        )
        return [dict(row) for row in rows]


# ============================================================
# travel_history
# ============================================================

async def save_travel_history(
    user_id: str,
    session_id: uuid.UUID,
    destination: Optional[str] = None,
    origin: Optional[str] = None,
    travel_date: Optional[str] = None,
    travel_days: Optional[int] = None,
    budget: Optional[float] = None,
    plan_summary: Optional[str] = None,
    status: str = "planned",
) -> int:
    """保存旅行历史记录"""
    pool = get_pool()
    from datetime import date as date_type

    travel_date_parsed = None
    if travel_date:
        try:
            travel_date_parsed = date_type.fromisoformat(travel_date)
        except ValueError:
            pass

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO travel_history
                (user_id, session_id, destination, origin, travel_date,
                 travel_days, budget, plan_summary, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            user_id, session_id, destination, origin, travel_date_parsed,
            travel_days, budget, plan_summary, status,
        )
        return row["id"]


async def get_travel_history(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """获取用户旅行历史"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, session_id, destination, origin,
                   travel_date, travel_days, budget, plan_summary, status, created_at
            FROM travel_history
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id, limit,
        )
        return [dict(row) for row in rows]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd E:\my\smart-travel\multi-agents && python -m pytest tests/test_db_models.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd E:\my\smart-travel\multi-agents
git add db/models.py tests/test_db_models.py
git commit -m "feat(db): add async CRUD models for all tables"
```

---

### Task 3: CRUD Models — user_preferences + conversation_summaries + travel_history

**Files:**
- Modify: `multi-agents/tests/test_db_models.py` (追加测试)

**Interfaces:**
- Consumes: `db.postgres.get_pool()`, Task 2 中已实现的函数
- Produces: (已在 Task 2 的 models.py 中实现，此 Task 仅补全测试覆盖)
  - `db.models.get_preferences(user_id) -> Optional[dict]`
  - `db.models.upsert_preferences(user_id, **fields) -> None`
  - `db.models.save_summary(user_id, session_id, summary, key_points?) -> int`
  - `db.models.get_user_summaries(user_id, limit?) -> list[dict]`
  - `db.models.save_travel_history(user_id, session_id, ...) -> int`
  - `db.models.get_travel_history(user_id, limit?) -> list[dict]`

- [ ] **Step 1: 追加偏好/摘要/历史的测试**

Append to `multi-agents/tests/test_db_models.py`:

```python
@pytest.mark.asyncio
async def test_upsert_and_get_preferences():
    """测试用户偏好的创建和更新"""
    from db.models import upsert_preferences, get_preferences

    # 首次插入
    await upsert_preferences(
        "test_user",
        travel_style=["古镇", "自然风光"],
        budget_level="舒适型",
    )

    prefs = await get_preferences("test_user")
    assert prefs is not None
    assert "古镇" in prefs["travel_style"]
    assert prefs["budget_level"] == "舒适型"

    # 更新
    await upsert_preferences("test_user", budget_level="豪华型")
    prefs = await get_preferences("test_user")
    assert prefs["budget_level"] == "豪华型"
    assert "古镇" in prefs["travel_style"]  # 未更新的字段保持不变


@pytest.mark.asyncio
async def test_get_preferences_not_found():
    """测试不存在的用户返回 None"""
    from db.models import get_preferences

    result = await get_preferences("nonexistent_user")
    assert result is None


@pytest.mark.asyncio
async def test_save_and_get_summary():
    """测试对话摘要保存和读取"""
    from db.models import create_session, save_summary, get_user_summaries

    session_id = await create_session("test_user", "测试会话")

    summary_id = await save_summary(
        user_id="test_user",
        session_id=session_id,
        summary="用户想去杭州3日游，预算3000元",
        key_points=["杭州", "3天", "3000元"],
    )
    assert summary_id > 0

    summaries = await get_user_summaries("test_user")
    assert len(summaries) == 1
    assert "杭州" in summaries[0]["summary"]
    assert "3天" in summaries[0]["key_points"]


@pytest.mark.asyncio
async def test_save_and_get_travel_history():
    """测试旅行历史保存和读取"""
    from db.models import create_session, save_travel_history, get_travel_history

    session_id = await create_session("test_user", "杭州之行")

    history_id = await save_travel_history(
        user_id="test_user",
        session_id=session_id,
        destination="杭州",
        origin="上海",
        travel_date="2026-08-15",
        travel_days=3,
        budget=3000.00,
        plan_summary="上海出发去杭州3日游",
        status="planned",
    )
    assert history_id > 0

    history = await get_travel_history("test_user")
    assert len(history) == 1
    assert history[0]["destination"] == "杭州"
    assert history[0]["budget"] == 3000.00
    assert history[0]["status"] == "planned"
```

- [ ] **Step 2: 运行全部测试验证通过**

Run: `cd E:\my\smart-travel\multi-agents && python -m pytest tests/test_db_models.py -v`
Expected: 10 tests PASS (5 from Task 2 + 5 new)

- [ ] **Step 3: Commit**

```bash
cd E:\my\smart-travel\multi-agents
git add tests/test_db_models.py
git commit -m "test(db): add full CRUD test coverage for preferences, summaries, travel history"
```

---

### Task 4: 更新 db/__init__.py 暴露完整接口 + 集成验证

**Files:**
- Modify: `multi-agents/db/__init__.py`
- Create: `multi-agents/tests/test_db_integration.py`

**Interfaces:**
- Consumes: Task 1-3 的所有模块
- Produces: 统一入口 `from db import init_db, close_db, get_pool, models`

- [ ] **Step 1: 更新 __init__.py**

Replace `multi-agents/db/__init__.py`:

```python
"""Database layer — PostgreSQL async connection pool + CRUD models."""
from .postgres import init_db, close_db, get_pool
from . import models

__all__ = ["init_db", "close_db", "get_pool", "models"]
```

- [ ] **Step 2: 写集成测试（模拟完整请求流程）**

Create `multi-agents/tests/test_db_integration.py`:

```python
"""
集成测试：模拟一个完整的用户请求生命周期
1. 创建会话
2. 保存用户消息
3. 保存助手回复（含 metadata）
4. 更新用户偏好
5. 保存对话摘要
6. 记录旅行历史
7. 验证所有数据可正确读取
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
async def setup_db():
    from db.postgres import init_db, close_db, get_pool

    await init_db()
    yield
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM chat_messages")
        await conn.execute("DELETE FROM conversation_summaries")
        await conn.execute("DELETE FROM travel_history")
        await conn.execute("DELETE FROM chat_sessions")
        await conn.execute("DELETE FROM user_preferences")
    await close_db()


@pytest.mark.asyncio
async def test_full_request_lifecycle():
    """模拟完整请求生命周期"""
    from db import models

    user_id = "integration_test_user"

    # 1. 创建会话
    session_id = await models.create_session(user_id, "杭州3日游规划")

    # 2. 保存用户消息
    await models.save_message(session_id, "user", "帮我规划去杭州3天，预算3000")

    # 3. 保存助手回复（含工具调用 metadata）
    await models.save_message(
        session_id, "assistant",
        "好的，我来帮你规划杭州3日游...",
        metadata={
            "tools_used": ["train_query", "gaode_weather"],
            "planner_context": {"destination": "杭州", "budget": 3000},
        },
    )

    # 4. 更新用户偏好
    await models.upsert_preferences(
        user_id,
        travel_style=["古镇", "自然风光"],
        budget_level="舒适型",
        liked_activities=["西湖", "灵隐寺"],
    )

    # 5. 保存对话摘要
    await models.save_summary(
        user_id=user_id,
        session_id=session_id,
        summary="用户从上海出发去杭州3日游，预算3000元，喜欢古镇和自然风光",
        key_points=["杭州", "3天", "3000元", "古镇"],
    )

    # 6. 记录旅行历史
    await models.save_travel_history(
        user_id=user_id,
        session_id=session_id,
        destination="杭州",
        origin="上海",
        travel_date="2026-08-15",
        travel_days=3,
        budget=3000.00,
        plan_summary="D1西湖→D2灵隐寺→D3西溪湿地",
    )

    # 7. 验证所有数据
    sessions = await models.get_user_sessions(user_id)
    assert len(sessions) == 1
    assert sessions[0]["title"] == "杭州3日游规划"

    messages = await models.get_session_messages(session_id)
    assert len(messages) == 2
    assert messages[1]["metadata"]["tools_used"] == ["train_query", "gaode_weather"]

    prefs = await models.get_preferences(user_id)
    assert "古镇" in prefs["travel_style"]

    summaries = await models.get_user_summaries(user_id)
    assert "杭州" in summaries[0]["summary"]

    history = await models.get_travel_history(user_id)
    assert history[0]["destination"] == "杭州"
    assert history[0]["plan_summary"] == "D1西湖→D2灵隐寺→D3西溪湿地"
```

- [ ] **Step 3: 运行所有测试**

Run: `cd E:\my\smart-travel\multi-agents && python -m pytest tests/test_db_postgres.py tests/test_db_models.py tests/test_db_integration.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
cd E:\my\smart-travel\multi-agents
git add db/__init__.py tests/test_db_integration.py
git commit -m "feat(db): finalize db layer with integration test"
```
