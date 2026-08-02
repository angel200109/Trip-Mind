"""
集成测试 — 模拟完整请求生命周期

使用 db 包的顶层接口（from db import models）验证所有 CRUD
操作在一次"旅行规划对话"中协同工作。
"""
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
    """
    模拟完整请求生命周期：
    1. 创建会话
    2. 保存多条消息（用户提问 + 助手回复 + 工具调用）
    3. 更新会话标题（根据对话内容自动命名）
    4. 更新用户偏好
    5. 保存对话摘要
    6. 保存旅行历史
    7. 验证所有数据读取正确
    """
    from db import models

    user_id = "integration_test_user"

    # ── Step 1: 创建会话 ────────────────────────────────────────
    session_id = await models.create_session(user_id, "新对话")
    assert isinstance(session_id, uuid.UUID), "session_id 应为 UUID"

    # 会话已出现在列表中
    sessions = await models.get_user_sessions(user_id)
    assert len(sessions) == 1
    assert sessions[0]["title"] == "新对话"

    # ── Step 2: 保存消息 ────────────────────────────────────────
    msg1_id = await models.save_message(session_id, "user", "帮我规划一次上海到杭州的3日游")
    msg2_id = await models.save_message(
        session_id, "assistant", "正在查询交通信息…",
        metadata={"tool": "train_query", "params": {"from": "上海", "to": "杭州"}},
    )
    msg3_id = await models.save_message(
        session_id, "assistant",
        "已为您规划：D1西湖，D2灵隐寺，D3古镇。高铁约1小时，推荐预算2000元。",
    )

    assert msg1_id > 0
    assert msg2_id > msg1_id
    assert msg3_id > msg2_id

    messages = await models.get_session_messages(session_id)
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "帮我规划一次上海到杭州的3日游"
    assert messages[1]["metadata"]["tool"] == "train_query"
    assert messages[1]["metadata"]["params"]["to"] == "杭州"
    assert messages[2]["role"] == "assistant"

    # ── Step 3: 更新会话标题 ────────────────────────────────────
    await models.update_session_title(session_id, "上海→杭州3日游规划")

    sessions_after = await models.get_user_sessions(user_id)
    assert sessions_after[0]["title"] == "上海→杭州3日游规划"

    # ── Step 4: 更新用户偏好 ────────────────────────────────────
    await models.upsert_preferences(
        user_id,
        budget_level="舒适型",
        travel_style=["自然风光", "历史文化", "美食"],
    )

    prefs = await models.get_preferences(user_id)
    assert prefs is not None
    assert prefs["user_id"] == user_id
    assert prefs["budget_level"] == "舒适型"
    assert "历史文化" in prefs["travel_style"]

    # ── Step 5: 保存对话摘要 ────────────────────────────────────
    summary_id = await models.save_summary(
        user_id,
        session_id,
        "用户规划了上海至杭州3日旅行，预算2000元，偏好自然和历史景点",
        key_points=["出发地：上海", "目的地：杭州", "天数：3天", "预算：2000元"],
    )
    assert summary_id > 0

    summaries = await models.get_user_summaries(user_id)
    assert len(summaries) == 1
    assert summaries[0]["summary"].startswith("用户规划了上海至杭州")
    assert "出发地：上海" in summaries[0]["key_points"]
    assert summaries[0]["session_id"] == session_id

    # ── Step 6: 保存旅行历史 ────────────────────────────────────
    history_id = await models.save_travel_history(
        user_id,
        session_id,
        destination="杭州",
        origin="上海",
        travel_date="2026-10-01",
        travel_days=3,
        budget=2000.0,
        plan_summary="西湖+灵隐寺+古镇三日游",
        status="planned",
    )
    assert history_id > 0

    history = await models.get_travel_history(user_id)
    assert len(history) == 1
    record = history[0]
    assert record["destination"] == "杭州"
    assert record["origin"] == "上海"
    assert record["travel_days"] == 3
    assert float(record["budget"]) == 2000.0
    assert record["plan_summary"] == "西湖+灵隐寺+古镇三日游"
    assert record["status"] == "planned"
    assert record["session_id"] == session_id

    # ── Step 7: 验证跨表数据一致性 ──────────────────────────────
    # 同一个 session_id 串联了消息、摘要、旅行历史
    assert summaries[0]["session_id"] == record["session_id"] == session_id

    # 删除会话后，消息应级联清除，但摘要和旅行历史保留
    await models.delete_session(session_id)

    msgs_after_delete = await models.get_session_messages(session_id)
    assert len(msgs_after_delete) == 0

    # 摘要和旅行历史不依赖会话外键，仍可读取
    summaries_still = await models.get_user_summaries(user_id)
    assert len(summaries_still) == 1

    history_still = await models.get_travel_history(user_id)
    assert len(history_still) == 1
