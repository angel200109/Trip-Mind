"""
SQLite → PostgreSQL 数据迁移脚本

把现有 SQLite 中的会话和消息迁移到 PostgreSQL。
- 会话 ID 用确定性 UUID（uuid5），迁移可重复执行（幂等）
- 消息 role 映射：SQLite 的 "ai" → PG 的 "assistant"

用法:
    python scripts/migrate_sqlite_to_pg.py
"""
import sys
import os
import asyncio
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from chat_history_manager import get_chat_history_manager
from db.postgres import init_db, close_db
from db import models


def deterministic_uuid(session_id: str) -> uuid.UUID:
    """基于原 session_id 生成确定性 UUID，保证重复迁移不产生重复数据"""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"smart-travel:{session_id}")


async def migrate():
    await init_db()

    mgr = get_chat_history_manager()
    sessions = mgr.get_user_sessions(limit=500)
    print(f"从 SQLite 读取到 {len(sessions)} 个会话")

    migrated = 0
    skipped = 0
    total_messages = 0

    for s in sessions:
        new_id = deterministic_uuid(s.session_id)
        title = s.title or "新的对话"

        try:
            await models.create_session_with_id(
                new_id,
                user_id=s.user_id,
                title=title,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
        except Exception as e:
            if "duplicate key" in str(e).lower():
                skipped += 1
                print(f"  [SKIP] {s.session_id} 已存在（幂等）")
                continue
            print(f"  [FAIL] {s.session_id}: {e}")
            continue

        # 迁移消息
        messages = mgr.get_session_messages(s.session_id)
        count = 0
        for m in messages:
            role = "assistant" if m.message_type == "ai" else "user"
            try:
                await models.save_message(new_id, role, m.content)
                count += 1
            except Exception as e:
                print(f"  [WARN] 消息迁移失败 ({m.message_type}): {e}")
        total_messages += count

        migrated += 1
        print(f"  [OK] {s.session_id} -> {new_id} ({count} 条消息)")

    # 验证
    pg_sessions = await models.get_user_sessions("default_user", limit=500)
    print()
    print(f"迁移完成: {migrated} 个会话, {total_messages} 条消息, 跳过 {skipped}")
    print(f"PG 中现有 {len(pg_sessions)} 个会话")

    await close_db()


if __name__ == "__main__":
    asyncio.run(migrate())
