"""
验证 Redis 短期记忆真实启用（非内存降级）
需要 Redis 运行在 localhost:6379（docker compose up -d）
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_redis_available():
    """确认短期记忆真正连接 Redis"""
    from memory.short_term import ShortTermMemory

    async def _t():
        stm = ShortTermMemory()
        assert stm._redis_available is None  # 未连接前
        await stm.add_message("probe_session", "user", "探针")
        assert stm._redis_available is True  # 连接成功
        assert stm._redis is not None  # 使用 Redis 而非降级
        await stm.clear("probe_session")

    asyncio.run(_t())


def test_redis_read_write():
    """写入后可读回，TTL 为 30 分钟"""
    from memory.short_term import ShortTermMemory

    async def _t():
        stm = ShortTermMemory()
        await stm.add_message("rw_session", "user", "你好")
        await stm.add_message("rw_session", "assistant", "你好！")

        history = await stm.get_history("rw_session")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "你好！"

        # 验证 TTL
        redis = stm._redis
        key = stm._session_key("rw_session")
        ttl = await redis.ttl(key)
        assert 0 < ttl <= 1800

        await stm.clear("rw_session")
        history2 = await stm.get_history("rw_session")
        assert len(history2) == 0

    asyncio.run(_t())


if __name__ == "__main__":
    test_redis_available()
    print("[OK] test_redis_available")
    test_redis_read_write()
    print("[OK] test_redis_read_write")
    print("All Redis live tests passed!")
