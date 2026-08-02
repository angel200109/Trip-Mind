-- 001_init.sql
-- 初始化所有表结构

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. 会话表
CREATE TABLE IF NOT EXISTS chat_sessions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       VARCHAR(64) NOT NULL,
    title         VARCHAR(200),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON chat_sessions(user_id, updated_at DESC);

-- 2. 消息表
CREATE TABLE IF NOT EXISTS chat_messages (
    id            BIGSERIAL PRIMARY KEY,
    session_id    UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role          VARCHAR(20) NOT NULL,
    content       TEXT NOT NULL,
    metadata      JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id, created_at);

-- 3. 用户偏好表
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id             VARCHAR(64) PRIMARY KEY,
    travel_style        TEXT[] DEFAULT '{}',
    budget_level        VARCHAR(20) DEFAULT '舒适型',
    hotel_preference    TEXT[] DEFAULT '{}',
    liked_activities    TEXT[] DEFAULT '{}',
    disliked_activities TEXT[] DEFAULT '{}',
    cuisine_preference  TEXT[] DEFAULT '{}',
    transport_priority  TEXT[] DEFAULT ARRAY['性价比','时间'],
    extra               JSONB DEFAULT '{}',
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- 4. 对话摘要归档
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id            SERIAL PRIMARY KEY,
    user_id       VARCHAR(64) NOT NULL,
    session_id    UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    summary       TEXT NOT NULL,
    key_points    TEXT[],
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_summaries_user ON conversation_summaries(user_id, created_at DESC);

-- 5. 旅行历史
CREATE TABLE IF NOT EXISTS travel_history (
    id            SERIAL PRIMARY KEY,
    user_id       VARCHAR(64) NOT NULL,
    session_id    UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    destination   VARCHAR(100),
    origin        VARCHAR(100),
    travel_date   DATE,
    travel_days   INT,
    budget        NUMERIC(10,2),
    plan_summary  TEXT,
    status        VARCHAR(20) DEFAULT 'planned',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_history_user ON travel_history(user_id, created_at DESC);

-- 迁移版本记录表
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INT PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT NOW()
);
