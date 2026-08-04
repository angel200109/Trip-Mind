"""
测试 server.py lifespan 初始化 PostgreSQL 连接池
需要 PostgreSQL 运行在 localhost:5432（docker compose up -d）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_init_db_makes_pool_available():
    """init_db() 后 db.models 的 CRUD 不再抛 RuntimeError"""
    import asyncio
    from db.postgres import init_db, close_db, get_pool

    async def _run():
        await init_db()
        pool = get_pool()
        assert pool is not None

        # 能执行查询
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
            assert result == 1

        # 记忆系统依赖的 CRUD 可用
        from db.models import get_preferences
        prefs = await get_preferences("lifespan_test_user")
        assert prefs is None or isinstance(prefs, dict)

        await close_db()

    asyncio.run(_run())


def test_init_db_idempotent():
    """init_db() 可重复调用（lifespan 重启场景）"""
    import asyncio
    from db.postgres import init_db, close_db

    async def _run():
        await init_db()
        await init_db()  # 第二次调用不应报错
        await close_db()

    asyncio.run(_run())


def test_server_import_with_lifespan():
    """server.py 能正常导入，lifespan 已注册"""
    import server
    assert server.app is not None
    assert hasattr(server.app, "router")
    # lifespan 存在（FastAPI 应用有 lifespan 属性或 router.lifespan_context）
    assert getattr(server.app.router, "lifespan_context", None) is not None


if __name__ == "__main__":
    test_init_db_makes_pool_available()
    test_init_db_idempotent()
    test_server_import_with_lifespan()
    print("All lifespan tests passed!")
