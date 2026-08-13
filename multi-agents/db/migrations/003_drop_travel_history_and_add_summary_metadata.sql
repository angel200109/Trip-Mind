-- 003_drop_travel_history_and_add_summary_metadata.sql
-- 移除旅行历史表 + 为上下文压缩功能扩展 conversation_summaries

DROP TABLE IF EXISTS travel_history;

-- 为 conversation_summaries 添加压缩元数据列
ALTER TABLE conversation_summaries
    ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

-- 按 session 快速查询最新摘要
CREATE INDEX IF NOT EXISTS idx_summaries_session
    ON conversation_summaries(session_id, created_at DESC);
