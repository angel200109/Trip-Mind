"""流式对话服务 - 调用 multi-agents + 生成 SSE 事件流"""
import uuid
import json
from typing import AsyncGenerator, Optional, Dict, Any, List

from langchain_core.messages import HumanMessage, AIMessage
from graph.workflow import travel_graph
from graph.state import GlobalState
from schemas.models import ChatRequest
from services.conversation_service import get_conversation_service
from services.stream_session import get_stream_session_manager


# Agent 节点进度文案（使用 workflow.py 中实际定义的节点名称）
AGENT_STATUS_MAP = {
    "main": "正在分析您的需求...",
    "planner": "正在规划行程方案...",
    "executor": "正在执行查询任务...",
    "summarizer": "正在整理旅行方案...",
    "feedback": "正在处理您的反馈...",
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


def build_state_from_messages(messages: List[dict]) -> GlobalState:
    """从前端消息列表构建 GlobalState"""
    langchain_messages = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        # 跳过 progress 占位消息
        if role == "assistant" and (not content or msg.get("progress")):
            continue
        if role == "user":
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
        "planner_context": None,
        "executor_context": None,
        "summarizer_context": None,
        "current_agent": None,
        "next_agent": None,
        "is_complete": False,
        "needs_replan": None,
        "feedback_type": None,
        "confirmation_message": None,
        "preference_updates": None,
    }


async def stream_chat(request: ChatRequest) -> AsyncGenerator[str, None]:
    """
    核心流式对话生成器

    1. 解析请求，构建 GlobalState
    2. 处理重连（如果带 requestId + lastChunkId）
    3. 用 astream_events 执行 graph，拦截事件生成 SSE chunk
    """
    session_mgr = get_stream_session_manager()
    conv_service = get_conversation_service()

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

    # 确保有 conversation
    conversation_id = request.conversationId
    if not conversation_id:
        conv = conv_service.create_conversation()
        conversation_id = conv.id

    # 创建 stream session
    session_mgr.create(request_id, conversation_id)

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
    session_mgr.append_chunk(request_id, meta_chunk)
    yield format_sse("message", meta_chunk)

    # 保存用户消息到数据库
    user_query = ""
    for msg in reversed(request.chatMessages):
        if msg.role == "user":
            content = msg.content
            if isinstance(content, list):
                user_query = next((c.get("text", "") for c in content if c.get("type") == "text"), str(content))
            else:
                user_query = str(content)
            break

    conv_service.add_message(conversation_id, "user", user_query)

    # 构建 state 并执行 graph
    messages_dicts = [{"role": m.role, "content": m.content} for m in request.chatMessages]
    state = build_state_from_messages(messages_dicts)

    # 跟踪已发送的 status 事件（避免重复）
    sent_agents: set = set()
    sent_tools: set = set()
    final_content = ""

    try:
        async for event in travel_graph.astream_events(state, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")

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

            # Summarizer LLM 流式 token
            elif kind == "on_chat_model_stream":
                # 通过 langgraph_node 判断是否是 summarizer 节点（节点名为 "summarizer"）
                metadata = event.get("metadata", {})
                node_name = metadata.get("langgraph_node", "")
                if node_name == "summarizer":
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

    # 如果没有从 streaming 拿到内容（fallback：从最终 state 提取）
    if not final_content:
        try:
            result = await travel_graph.ainvoke(state)
            summarizer_ctx = result.get("summarizer_context") or {}
            answer = summarizer_ctx.get("final_summary", "")
            if not answer:
                planner_ctx = result.get("planner_context") or {}
                answer = planner_ctx.get("clarification_question", "处理完成")

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
        conv_service.add_message(conversation_id, "assistant", final_content)

    # 发送 done 事件
    chunk_id += 1
    done_chunk = {"requestId": request_id, "chunkId": chunk_id, "done": True}
    session_mgr.append_chunk(request_id, done_chunk)
    session_mgr.mark_done(request_id)
    yield format_sse("done", done_chunk)
