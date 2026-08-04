"""
测试 upsert_preferences 的白名单过滤与数组合并
需要 PostgreSQL 运行（docker compose up -d）
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(coro):
    """在共享事件循环中运行（asyncpg pool 不能跨 loop）"""
    return asyncio.run(coro)


async def _cleanup(users):
    from db.postgres import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        placeholders = ",".join([f"'{u}'" for u in users])
        await conn.execute(
            f"DELETE FROM user_preferences WHERE user_id IN ({placeholders})"
        )


def test_whitelist_filters_unknown_fields():
    """未知字段被丢弃，合法字段正常写入"""
    from db.postgres import init_db, close_db
    from db import models

    async def _t():
        await init_db()
        await models.upsert_preferences(
            "whitelist_test", unknown_field="x", budget_level="舒适型"
        )
        p = await models.get_preferences("whitelist_test")
        assert "unknown_field" not in p
        assert p["budget_level"] == "舒适型"
        await _cleanup(["whitelist_test"])
        await close_db()

    _run(_t())


def test_array_fields_append_and_dedup():
    """数组字段 append + 去重"""
    from db.postgres import init_db, close_db
    from db import models

    async def _t():
        await init_db()
        await models.upsert_preferences("merge_test", liked_activities=["古镇"])
        await models.upsert_preferences("merge_test", liked_activities=["古镇", "自然风光"])
        p = await models.get_preferences("merge_test")
        assert p["liked_activities"] == ["古镇", "自然风光"]
        await _cleanup(["merge_test"])
        await close_db()

    _run(_t())


def test_scalar_fields_overwrite():
    """标量字段整体覆盖"""
    from db.postgres import init_db, close_db
    from db import models

    async def _t():
        await init_db()
        await models.upsert_preferences("scalar_test", budget_level="经济型")
        await models.upsert_preferences("scalar_test", budget_level="舒适型")
        p = await models.get_preferences("scalar_test")
        assert p["budget_level"] == "舒适型"
        await _cleanup(["scalar_test"])
        await close_db()

    _run(_t())


if __name__ == "__main__":
    test_whitelist_filters_unknown_fields()
    print("[OK] test_whitelist_filters_unknown_fields")
    test_array_fields_append_and_dedup()
    print("[OK] test_array_fields_append_and_dedup")
    test_scalar_fields_overwrite()
    print("[OK] test_scalar_fields_overwrite")
    print("All upsert_preferences tests passed!")
