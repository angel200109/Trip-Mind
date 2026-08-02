"""
PostgreSQL 连接池管理
- asyncpg 连接池
- 自动执行迁移脚本
- 全局单例
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import asyncpg

from config.settings import DATABASE_URL

_pool: Optional[asyncpg.Pool] = None

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_pool() -> asyncpg.Pool:
    """获取全局连接池（必须先调用 init_db）"""
    if _pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _pool


async def init_db() -> None:
    """初始化连接池并执行迁移"""
    global _pool
    if _pool is not None:
        return

    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )

    await _run_migrations()


async def close_db() -> None:
    """关闭连接池"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _run_migrations() -> None:
    """按版本号顺序执行未应用的迁移脚本"""
    pool = get_pool()

    async with pool.acquire() as conn:
        # 确保迁移记录表存在
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INT PRIMARY KEY,
                applied_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # 获取已应用的版本
        applied = set()
        rows = await conn.fetch("SELECT version FROM schema_migrations")
        for row in rows:
            applied.add(row["version"])

        # 扫描迁移文件并按版本排序
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

        for file_path in migration_files:
            version = int(file_path.stem.split("_")[0])
            if version in applied:
                continue

            sql = file_path.read_text(encoding="utf-8")
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1)", version
            )
            print(f"[DB] Applied migration: {file_path.name}")
