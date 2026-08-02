"""测试 db/models.py 的 CRUD 操作（chat_sessions + chat_messages）"""
import pytest
import pytest_asyncio
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """每个测试前初始化DB，测试后清理"""
    from db.postgres import init_db, close_db, get_pool

    await init_db()
    yield
    # 清理测试数据（依赖关系：先删子表，再删父表）
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
