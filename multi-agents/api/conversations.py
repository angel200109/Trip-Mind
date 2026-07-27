"""会话管理 API 路由"""
from fastapi import APIRouter, HTTPException
from schemas.models import ApiResponse, CreateConversationRequest, UpdateTitleRequest
from services.conversation_service import get_conversation_service

router = APIRouter()


@router.get("/conversations")
async def list_conversations():
    service = get_conversation_service()
    conversations = service.list_conversations()
    return ApiResponse(data=[c.model_dump() for c in conversations])


@router.get("/conversations/{session_id}")
async def get_conversation(session_id: str):
    service = get_conversation_service()
    conversation = service.get_conversation(session_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return ApiResponse(data=conversation.model_dump())


@router.post("/conversations")
async def create_conversation(req: CreateConversationRequest):
    service = get_conversation_service()
    conversation = service.create_conversation(title=req.title)
    return ApiResponse(data=conversation.model_dump())


@router.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str):
    service = get_conversation_service()
    service.delete_conversation(session_id)
    return ApiResponse(data=True)


@router.patch("/conversations/{session_id}/title")
async def update_title(session_id: str, req: UpdateTitleRequest):
    service = get_conversation_service()
    service.update_title(session_id, req.title)
    return ApiResponse(data=True)
