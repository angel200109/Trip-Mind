-- 002_add_pref_fields.sql
-- 扩展 user_preferences：补齐 JSON 画像独有字段
ALTER TABLE user_preferences
    ADD COLUMN IF NOT EXISTS max_daily_budget           NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS dietary_restrictions       TEXT[] DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS room_type_preference       VARCHAR(50),
    ADD COLUMN IF NOT EXISTS destination_types          TEXT[] DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS travel_season_preference   TEXT[] DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS daily_schedule_preference  VARCHAR(20) DEFAULT '随性';
