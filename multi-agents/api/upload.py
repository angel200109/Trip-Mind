"""文件上传 API 路由"""
import time
import os
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from schemas.models import ApiResponse

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "images"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/jpg"}


@router.post("/uploadFile")
async def upload_file(file: UploadFile = File(...)):
    """上传图片文件"""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=442, detail="图片格式错误，仅支持 png/jpeg/webp")

    ext = file.filename.split(".")[-1] if file.filename else "png"
    filename = f"{int(time.time() * 1000)}.{ext}"
    filepath = UPLOAD_DIR / filename

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    host = os.getenv("UPLOAD_HOST", "localhost:8000")
    url = f"{host}/static/images/{filename}"
    return ApiResponse(data=url)
