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
