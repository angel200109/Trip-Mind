"""流式对话服务 - 调用 multi-agents + 生成 SSE 事件流"""
import uuid
import json
from typing import AsyncGenerator, Optional, Dict, Any, List

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from graph.workflow import travel_graph
from graph.state import GlobalState
from schemas.models import ChatRequest
from services.conversation_service import get_conversation_service
from services.stream_session import get_stream_session_manager
from db import models as db_models


def _uuid_or_none(session_id: str) -> Optional[uuid.UUID]:
    """字符串转 UUID，非法返回 None"""
    try:
        return uuid.UUID(str(session_id))
    except (ValueError, TypeError):
        return None


# Agent 节点进度文案（使用 workflow.py 中实际定义的节点名称）
AGENT_STATUS_MAP = {
    "intent_router": "正在分析您的需求...",
    "react_executor": "正在查询相关信息...",
    "planner": "正在规划行程方案...",
    "step_executor": "正在执行查询任务...",
    "summarizer": "正在整理旅行方案...",
}

# 工具调用进度文案
TOOL_STATUS_MAP = {
    "train_query": "正在查询火车票信息...",
    "gaode_weather": "正在查询目的地天气...",
    "gaode_hotel_search": "正在搜索酒店...",
    "gaode_poi_search": "正在搜索景点...",
    "gaode_routing": "正在规划路线...",
    "rag_search": "正在查阅旅游攻略...",
    "flight_query": "正在查询航班...",
    "lucky_day": "正在查询黄历吉日...",
    "biying_search": "正在搜索相关信息...",
}


def format_sse(event: str, data: dict) -> str:
    """格式化 SSE 事件字符串"""
    chunk_id = data.get("chunkId", 0)
    data_str = json.dumps(data, ensure_ascii=False)
    return f"id: {chunk_id}\nevent: {event}\ndata: {data_str}\n\n"


def build_state_from_messages(messages: List[dict], pg_session_id: Optional[str] = None) -> GlobalState:
    """从前端消息列表构建 GlobalState"""
    langchain_messages = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        # 跳过 progress 占位消息
        if role == "assistant" and (not content or msg.get("progress")):
            continue
        if role == "system" and content:
            langchain_messages.append(SystemMessage(content=content))
        elif role == "user":
            if isinstance(content, list):
                # 多模态消息，取文本部分
                text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
                langchain_messages.append(HumanMessage(content=text))
            else:
                langchain_messages.append(HumanMessage(content=content))
        elif role == "assistant" and content:
            langchain_messages.append(AIMessage(content=content))

    # 获取最后一条用户消息作为 user_query
    user_query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                user_query = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
            else:
                user_query = content
            break

    return {
        "user_query": user_query,
        "messages": langchain_messages,
        "intent_context": None,
        "executor_context": None,
        "summarizer_context": None,
        "current_agent": None,
        "next_agent": None,
        "is_complete": False,
        "final_answer": None,
        "pg_session_id": pg_session_id,
    }


async def stream_chat(request: ChatRequest) -> AsyncGenerator[str, None]:
    """
    核心流式对话生成器

    1. 解析请求，构建 GlobalState
    2. 处理重连（如果带 requestId + lastChunkId）
    3. 用 astream_events 执行 graph，拦截事件生成 SSE chunk
    """
    print("[stream_chat] === START ===", flush=True)
    session_mgr = get_stream_session_manager()
    conv_service = get_conversation_service()
    print("[stream_chat] services OK", flush=True)

    # 处理重连
    if request.requestId and request.lastChunkId and request.lastChunkId > 0:
        existing = session_mgr.get(request.requestId)
        if existing:
            # 重放缺失的 chunk
            missed = session_mgr.replay_from(request.requestId, request.lastChunkId)
            for chunk in missed:
                yield format_sse("message", chunk)
            if existing.status == "done":
                yield format_sse("done", {"requestId": request.requestId, "chunkId": len(existing.chunks) + 1, "done": True})
            return

    # 新请求
    request_id = request.requestId or str(uuid.uuid4())
    print(f"[stream_chat] request_id={request_id}", flush=True)

    # 确保有 conversation
    conversation_id = request.conversationId
    if not conversation_id:
        conv = await conv_service.create_conversation()
        conversation_id = conv.id
    print(f"[stream_chat] conversation_id={conversation_id}", flush=True)

    # 创建 stream session
    try:
        session_mgr.create(request_id, conversation_id)
        print("[stream_chat] session created OK", flush=True)
    except Exception as e:
        print(f"[stream_chat] session create FAILED: {e}", flush=True)
        import traceback; traceback.print_exc()
        return

    chunk_id = 0

    # 发送 meta 事件
    chunk_id += 1
    meta_chunk = {
        "requestId": request_id,
        "chunkId": chunk_id,
        "type": "meta",
        "functionName": "",
        "data": "",
        "conversationId": conversation_id,
    }
    try:
        session_mgr.append_chunk(request_id, meta_chunk)
        print("[stream_chat] append_chunk OK", flush=True)
    except Exception as e:
        print(f"[stream_chat] append_chunk FAILED: {e}", flush=True)
        import traceback; traceback.print_exc()
        return

    print("[stream_chat] yielding meta chunk...", flush=True)
    yield format_sse("message", meta_chunk)
    print("[stream_chat] meta chunk yielded OK", flush=True)

    # 用户提问直接来自请求（前端只传当前消息）
    user_query = request.userQuery
    if not user_query:
        print("[stream_chat] WARN: userQuery 为空", flush=True)

    # 1. 从 PG 读取会话历史（不含当前消息，权威来源）
    detail = await conv_service.get_conversation(conversation_id)
    history_messages: List[dict] = []
    if detail:
        history_messages = [{"role": m.role, "content": m.content} for m in detail.messages]

    # 2. 重新生成检测：PG 历史最后一条已是同内容 user 消息（前端 regenerate 场景）
    #    → 删除旧的 assistant 回答，不重复追加 user 消息
    is_regenerate = False
    if history_messages and history_messages[-1]["role"] == "user":
        last_user_content = history_messages[-1]["content"]
        if str(last_user_content) == user_query:
            is_regenerate = True
            sid = _uuid_or_none(conversation_id)
            if sid:
                await db_models.delete_last_assistant_message(sid)
                print("[stream_chat] 检测到重新生成，已删除旧回答", flush=True)

    if not is_regenerate:
        # 正常对话：保存用户消息（chat_service 是消息持久化唯一入口）
        await conv_service.add_message(conversation_id, "user", user_query)
        # 追加当前消息到历史（供构建对话上下文）
        history_messages.append({"role": "user", "content": user_query})

    print(f"[stream_chat] user_query={user_query}", flush=True)
    print(f"[stream_chat] 历史 {len(history_messages)} 条（重新生成={is_regenerate}）", flush=True)

    # 2.5 上下文压缩（超过阈值时压缩旧消息，对 graph 透明）
    from services.context_compression_service import compress_if_needed
    history_messages = await compress_if_needed(
        session_id=conversation_id,
        all_messages=history_messages,
        user_id="default_user",
    )

    # 3. 构建 state 并执行 graph（带上 PG 会话 ID 供记忆写回）
    state = build_state_from_messages(history_messages, pg_session_id=conversation_id)
    # 确保 user_query 使用当前提问（历史提取可能为空）
    state["user_query"] = user_query
    print("[stream_chat] state built, calling astream_events...", flush=True)

    # 跟踪已发送的 status 事件（避免重复）
    sent_agents: set = set()
    sent_tools: set = set()
    final_content = ""

    streaming_started = False
    try:
        async for event in travel_graph.astream_events(state, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")
            if kind == "on_chat_model_stream":
                if not streaming_started:
                    print(f"[stream_chat] LLM streaming START ({name})", flush=True)
                    streaming_started = True
            else:
                if streaming_started:
                    print(f"[stream_chat] LLM streaming END", flush=True)
                    streaming_started = False
                print(f"[stream_chat] event: {kind} | {name}", flush=True)

            # Agent 节点开始（节点名与 workflow.py 保持一致）
            if kind == "on_chain_start" and name in AGENT_STATUS_MAP:
                if name not in sent_agents:
                    sent_agents.add(name)
                    chunk_id += 1
                    status_chunk = {
                        "requestId": request_id,
                        "chunkId": chunk_id,
                        "type": "status",
                        "functionName": name,
                        "data": AGENT_STATUS_MAP[name],
                    }
                    session_mgr.append_chunk(request_id, status_chunk)
                    yield format_sse("message", status_chunk)

            # 工具调用开始
            elif kind == "on_tool_start" and name in TOOL_STATUS_MAP:
                if name not in sent_tools:
                    sent_tools.add(name)
                    chunk_id += 1
                    tool_chunk = {
                        "requestId": request_id,
                        "chunkId": chunk_id,
                        "type": "status",
                        "functionName": name,
                        "data": TOOL_STATUS_MAP[name],
                    }
                    session_mgr.append_chunk(request_id, tool_chunk)
                    yield format_sse("message", tool_chunk)

            # LLM 流式 token（统一最终输出层：summarizer + main 节点的回答）
            elif kind == "on_chat_model_stream":
                # 通过 langgraph_node 判断节点：summarizer 或 main
                metadata = event.get("metadata", {})
                node_name = metadata.get("langgraph_node", "")
                # main 节点的分类器 LLM 用 tags 标记，跳过其输出（只输出真正的回答）
                tags = event.get("tags") or []
                if node_name in ("summarizer", "intent_router") and "intent_classifier" not in tags:
                    chunk_content = event.get("data", {}).get("chunk", None)
                    if chunk_content and hasattr(chunk_content, "content") and chunk_content.content:
                        token = chunk_content.content
                        final_content += token
                        chunk_id += 1
                        content_chunk = {
                            "requestId": request_id,
                            "chunkId": chunk_id,
                            "type": "content",
                            "functionName": "",
                            "data": token,
                        }
                        session_mgr.append_chunk(request_id, content_chunk)
                        yield format_sse("message", content_chunk)

    except Exception as e:
        import traceback
        print(f"[stream_chat] ERROR: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        chunk_id += 1
        error_chunk = {
            "requestId": request_id,
            "chunkId": chunk_id,
            "type": "error",
            "functionName": "",
            "data": f"服务处理出错: {str(e)}",
        }
        session_mgr.append_chunk(request_id, error_chunk)
        session_mgr.mark_error(request_id)
        yield format_sse("error", error_chunk)
        return

    # 如果没有从 streaming 拿到内容（fallback：从最终 state 统一提取）
    if not final_content:
        try:
            result = await travel_graph.ainvoke(state)

            # 统一最终输出层：所有模式的回答都写入 final_answer
            answer = result.get("final_answer", "")

            # 兜底：从各上下文提取
            if not answer:
                answer = (result.get("summarizer_context") or {}).get("final_summary", "")
            if not answer:
                answer = (result.get("planner_context") or {}).get("clarification_question", "")
            if not answer:
                answer = "处理完成"

            # 一次性发送完整内容
            chunk_id += 1
            content_chunk = {
                "requestId": request_id,
                "chunkId": chunk_id,
                "type": "content",
                "functionName": "",
                "data": answer,
            }
            session_mgr.append_chunk(request_id, content_chunk)
            yield format_sse("message", content_chunk)
            final_content = answer
        except Exception as e:
            chunk_id += 1
            error_chunk = {
                "requestId": request_id,
                "chunkId": chunk_id,
                "type": "error",
                "functionName": "",
                "data": f"服务处理出错: {str(e)}",
            }
            session_mgr.append_chunk(request_id, error_chunk)
            session_mgr.mark_error(request_id)
            yield format_sse("error", error_chunk)
            return

    # 保存 AI 回复到数据库
    if final_content:
        await conv_service.add_message(conversation_id, "assistant", final_content)

    # 发送 done 事件
    chunk_id += 1
    done_chunk = {"requestId": request_id, "chunkId": chunk_id, "done": True}
    session_mgr.append_chunk(request_id, done_chunk)
    session_mgr.mark_done(request_id)
    yield format_sse("done", done_chunk)
