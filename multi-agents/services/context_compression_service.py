"""
增量上下文压缩服务

在 chat_service 的"加载历史"与"构建 state"之间调用，对 LangGraph 完全透明。

逻辑：
- 无缓存摘要 + 消息数 <= 阈值 → 原样返回
- 无缓存摘要 + 消息数 > 阈值 → 首次压缩，保存摘要
- 有缓存摘要 + 摘要之后新增消息 <= 阈值 → 拼接 [摘要 + 最近消息]
- 有缓存摘要 + 新增消息 > 阈值 → 增量再压缩
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from db.models import get_session_summary, save_summary
from tools.context_compressor import compress_messages

COMPRESS_THRESHOLD = 20
RECENT_MESSAGES_KEEP = 10


async def compress_if_needed(
    session_id: str,
    all_messages: List[dict],
    user_id: str = "default_user",
) -> List[dict]:
    """
    根据消息量和缓存摘要决定是否压缩。

    Args:
        session_id: 会话 ID（PG UUID 字符串）
        all_messages: 完整历史消息 [{"role": ..., "content": ...}, ...]
        user_id: 用户 ID

    Returns:
        处理后的消息列表（可能包含摘要 system 消息 + 最近原文消息）
    """
    total = len(all_messages)

    # 消息太少，不需要压缩
    if total <= COMPRESS_THRESHOLD:
        # 但仍然检查是否有缓存摘要（上一次压缩过）
        sid = _parse_uuid(session_id)
        if sid:
            cached = await get_session_summary(sid)
            if cached:
                return _build_compressed_messages(cached["summary"], all_messages, cached["metadata"])
        return all_messages

    # 需要压缩：检查是否有缓存摘要
    sid = _parse_uuid(session_id)
    if not sid:
        return all_messages

    cached = await get_session_summary(sid)

    if cached:
        # 有缓存摘要 — 计算新增消息数
        summarized_up_to = (cached.get("metadata") or {}).get("summarized_up_to", 0)
        unsummarized = all_messages[summarized_up_to:]

        if len(unsummarized) <= COMPRESS_THRESHOLD:
            # 新增消息未超阈值，直接用缓存摘要 + 新增消息
            return _build_compressed_messages(cached["summary"], unsummarized, cached["metadata"])
        else:
            # 新增消息超阈值 → 增量再压缩
            messages_to_compress = unsummarized[:-RECENT_MESSAGES_KEEP]
            recent = unsummarized[-RECENT_MESSAGES_KEEP:]

            new_summary = await compress_messages(
                messages_to_compress,
                existing_summary=cached["summary"],
            )

            new_offset = total - RECENT_MESSAGES_KEEP
            await save_summary(
                user_id=user_id,
                session_id=sid,
                summary=new_summary,
                key_points=[],
                metadata={"summarized_up_to": new_offset, "message_count": total},
            )
            print(f"  [Compression] 增量压缩完成，覆盖到第 {new_offset} 条消息")

            return _make_summary_message(new_summary) + recent
    else:
        # 无缓存摘要 → 首次压缩
        messages_to_compress = all_messages[:-RECENT_MESSAGES_KEEP]
        recent = all_messages[-RECENT_MESSAGES_KEEP:]

        new_summary = await compress_messages(messages_to_compress)

        new_offset = total - RECENT_MESSAGES_KEEP
        await save_summary(
            user_id=user_id,
            session_id=sid,
            summary=new_summary,
            key_points=[],
            metadata={"summarized_up_to": new_offset, "message_count": total},
        )
        print(f"  [Compression] 首次压缩完成，覆盖到第 {new_offset} 条消息")

        return _make_summary_message(new_summary) + recent


def _build_compressed_messages(
    summary: str,
    recent_messages: List[dict],
    metadata: Optional[dict] = None,
) -> List[dict]:
    """拼接摘要消息 + 最近原文消息"""
    return _make_summary_message(summary) + recent_messages[-RECENT_MESSAGES_KEEP:]


def _make_summary_message(summary: str) -> List[dict]:
    """将摘要文本包装为 system 消息"""
    return [{"role": "system", "content": f"[对话摘要] {summary}"}]


def _parse_uuid(session_id: str) -> Optional[uuid.UUID]:
    """安全解析 UUID 字符串"""
    try:
        return uuid.UUID(str(session_id))
    except (ValueError, TypeError):
        return None
