"""流式对话 API 路由"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from schemas.models import ChatRequest
from services.chat_service import stream_chat

router = APIRouter()


@router.post("/chatMessage/stream")
async def chat_stream(request: ChatRequest):
    """SSE 流式对话端点"""
    return StreamingResponse(
        stream_chat(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
