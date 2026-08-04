"""FastAPI 服务入口"""
import sys
import os
from pathlib import Path

# Windows 终端默认 GBK 编码，强制 UTF-8 避免 emoji print 崩溃
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 PostgreSQL 连接池，关闭时释放"""
    from db.postgres import init_db, close_db

    try:
        await init_db()
        print("[DB] PostgreSQL 连接池已初始化")
    except Exception as e:
        print(f"[DB] PostgreSQL 初始化失败（降级运行）: {type(e).__name__}: {e}")
    yield
    try:
        await close_db()
        print("[DB] PostgreSQL 连接池已关闭")
    except Exception:
        pass


app = FastAPI(title="Smart Travel Multi-Agents API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（图片上传）
images_dir = Path(__file__).parent / "images"
images_dir.mkdir(exist_ok=True)
app.mount("/static/images", StaticFiles(directory=str(images_dir)), name="images")

# 注册路由
from api.chat import router as chat_router
from api.conversations import router as conversations_router
from api.upload import router as upload_router

app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(upload_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
