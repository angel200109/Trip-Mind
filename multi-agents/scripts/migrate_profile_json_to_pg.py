"""
JSON 用户画像 → PG user_preferences 迁移脚本

把 data/user_profiles/{user}.json 中的画像字段迁移到 PG。
数组字段通过 upsert_preferences 的 append+去重 语义合并，不覆盖已有值。

用法:
    python scripts/migrate_profile_json_to_pg.py [user_id]
"""
import sys
import asyncio
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import PROJECT_ROOT
from db.postgres import init_db, close_db
from db import models


# JSON 画像字段 → PG 字段映射（仅映射白名单内的字段）
FIELD_MAP = {
    "travel_style": "travel_style",
    "destination_types": "destination_types",
    "budget_level": "budget_level",
    "max_daily_budget": "max_daily_budget",
    "hotel_preference": "hotel_preference",
    "room_type_preference": "room_type_preference",
    "transport_priority": "transport_priority",
    "dietary_restrictions": "dietary_restrictions",
    "cuisine_preference": "cuisine_preference",
    "liked_activities": "liked_activities",
    "disliked_activities": "disliked_activities",
    "travel_season_preference": "travel_season_preference",
    "daily_schedule_preference": "daily_schedule_preference",
}


async def migrate(user_id: str = "default_user"):
    await init_db()

    profile_dir = PROJECT_ROOT / "data" / "user_profiles"
    profile_path = profile_dir / f"{user_id}.json"

    if not profile_path.exists():
        print(f"[SKIP] 画像文件不存在: {profile_path}")
        await close_db()
        return

    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    # 字段映射 + 白名单过滤（upsert_preferences 内部会再过滤一次）
    fields = {}
    for json_key, pg_key in FIELD_MAP.items():
        value = profile.get(json_key)
        if value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, str) and not value:
            continue
        fields[pg_key] = value

    if not fields:
        print(f"[SKIP] {user_id} 画像无有效字段")
        await close_db()
        return

    await models.upsert_preferences(user_id, **fields)
    print(f"[OK] {user_id} 画像已迁移: {list(fields.keys())}")

    # 验证
    saved = await models.get_preferences(user_id)
    if saved:
        print(f"[OK] PG 中已存在: budget_level={saved.get('budget_level')}, "
              f"travel_style={saved.get('travel_style')}")

    await close_db()


if __name__ == "__main__":
    user_id = sys.argv[1] if len(sys.argv) > 1 else "default_user"
    asyncio.run(migrate(user_id))
