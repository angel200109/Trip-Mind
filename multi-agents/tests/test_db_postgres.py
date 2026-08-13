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
