"""
测试 ConversationService（PostgreSQL 版）
需要 PostgreSQL 运行在 localhost:5432（docker compose up -d）
"""
import sys
import os
import asyncio
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(coro):
    """在测试函数中运行 async"""
    return asyncio.run(coro)


def test_list_conversations():
    """列表接口返回 ConversationListItem 且含 messageCount"""
    from services.conversation_service import get_conversation_service
    from db.postgres import init_db, close_db

    async def _t():
        await init_db()
        svc = get_conversation_service()
        items = await svc.list_conversations()
        assert isinstance(items, list)
        for item in items:
            assert item.id
            assert isinstance(item.messageCount, int)
            assert item.groupLabel == "全部对话"
        await close_db()

    _run(_t())


def test_create_and_get_conversation():
    """创建会话 -> 添加消息 -> 获取详情"""
    from services.conversation_service import get_conversation_service
    from db.postgres import init_db, close_db
    from db import models

    async def _t():
        await init_db()
        svc = get_conversation_service()

        # 创建
        conv = await svc.create_conversation("测试会话")
        assert uuid.UUID(conv.id)  # 是合法 UUID
        assert conv.messageCount == 0

        # 添加消息
        await svc.add_message(conv.id, "user", "你好")
        await svc.add_message(conv.id, "assistant", "你好！有什么可以帮你？")

        # 获取详情
        detail = await svc.get_conversation(conv.id)
        assert detail is not None
        assert detail.title == "测试会话"
        assert len(detail.messages) == 2
        assert detail.messages[0].role == "user"
        assert detail.messages[0].content == "你好"
        assert detail.messages[1].role == "assistant"

        # 列表验证 messageCount
        items = await svc.list_conversations()
        my = next((i for i in items if i.id == conv.id), None)
        assert my is not None
        assert my.messageCount == 2

        # 清理
        await svc.delete_conversation(conv.id)
        detail2 = await svc.get_conversation(conv.id)
        assert detail2 is None
        await close_db()

    _run(_t())


def test_get_conversation_invalid_id():
    """非法会话 ID 返回 None 而不是报错"""
    from services.conversation_service import get_conversation_service
    from db.postgres import init_db, close_db

    async def _t():
        await init_db()
        svc = get_conversation_service()
        assert await svc.get_conversation("not-a-uuid") is None
        await close_db()

    _run(_t())


def test_update_title():
    """更新会话标题"""
    from services.conversation_service import get_conversation_service
    from db.postgres import init_db, close_db

    async def _t():
        await init_db()
        svc = get_conversation_service()
        conv = await svc.create_conversation("旧标题")
        await svc.update_title(conv.id, "杭州3日游规划")

        detail = await svc.get_conversation(conv.id)
        assert detail.title == "杭州3日游规划"
        await svc.delete_conversation(conv.id)
        await close_db()

    _run(_t())


if __name__ == "__main__":
    test_list_conversations()
    print("[OK] test_list_conversations")
    test_create_and_get_conversation()
    print("[OK] test_create_and_get_conversation")
    test_get_conversation_invalid_id()
    print("[OK] test_get_conversation_invalid_id")
    test_update_title()
    print("[OK] test_update_title")
    print("All conversation service tests passed!")
