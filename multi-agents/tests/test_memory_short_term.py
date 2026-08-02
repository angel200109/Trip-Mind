"""测试 memory/short_term.py 短期记忆"""
import pytest
import pytest_asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.short_term import ShortTermMemory


@pytest_asyncio.fixture(autouse=True)
async def memory():
    """每个测试使用独立的 ShortTermMemory 实例并清理"""
    mem = ShortTermMemory()
    yield mem
    # 清理测试数据
    await mem.clear("test_session_1")
    await mem.clear("test_session_2")


@pytest.mark.asyncio
async def test_add_and_get_history(memory):
    """测试添加消息并读取历史"""
    await memory.add_message("test_session_1", "user", "你好")
    await memory.add_message("test_session_1", "assistant", "你好！有什么可以帮你？")

    history = await memory.get_history("test_session_1")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "你好"
    assert history[1]["role"] == "assistant"
    assert "timestamp" in history[0]


@pytest.mark.asyncio
async def test_sliding_window(memory):
    """测试滑动窗口淘汰（超过 max_turns 时只保留最近的）"""
    mem = ShortTermMemory(max_turns=5)

    for i in range(10):
        await mem.add_message("test_session_1", "user", f"消息{i}")

    history = await mem.get_history("test_session_1")
    assert len(history) == 5
    assert history[0]["content"] == "消息5"  # 最早保留的是第5条
    assert history[4]["content"] == "消息9"  # 最新的是第9条

    await mem.clear("test_session_1")


@pytest.mark.asyncio
async def test_session_isolation(memory):
    """测试不同 session 之间隔离"""
    await memory.add_message("test_session_1", "user", "会话1的消息")
    await memory.add_message("test_session_2", "user", "会话2的消息")

    h1 = await memory.get_history("test_session_1")
    h2 = await memory.get_history("test_session_2")

    assert len(h1) == 1
    assert len(h2) == 1
    assert h1[0]["content"] == "会话1的消息"
    assert h2[0]["content"] == "会话2的消息"


@pytest.mark.asyncio
async def test_get_history_last_n(memory):
    """测试 last_n 参数只返回最近 N 条"""
    for i in range(10):
        await memory.add_message("test_session_1", "user", f"消息{i}")

    history = await memory.get_history("test_session_1", last_n=3)
    assert len(history) == 3
    assert history[0]["content"] == "消息7"


@pytest.mark.asyncio
async def test_get_context_window(memory):
    """测试上下文窗口按 token 预算裁剪"""
    await memory.add_message("test_session_1", "user", "A" * 1000)
    await memory.add_message("test_session_1", "assistant", "B" * 1000)
    await memory.add_message("test_session_1", "user", "C" * 100)

    # 很小的 token 预算，应该只返回最近几条
    context = await memory.get_context_window("test_session_1", max_tokens=200)
    assert len(context) > 0
    assert "C" * 100 in context  # 最近一条一定在


@pytest.mark.asyncio
async def test_clear(memory):
    """测试清除指定 session"""
    await memory.add_message("test_session_1", "user", "test")
    await memory.clear("test_session_1")

    history = await memory.get_history("test_session_1")
    assert len(history) == 0


@pytest.mark.asyncio
async def test_fallback_when_redis_unavailable():
    """测试 Redis 不可用时降级到内存"""
    mem = ShortTermMemory(redis_url="redis://localhost:19999/0")  # 不存在的端口

    # 不应抛异常
    await mem.add_message("test_session_1", "user", "降级测试")
    history = await mem.get_history("test_session_1")

    assert len(history) == 1
    assert history[0]["content"] == "降级测试"

    await mem.clear("test_session_1")
