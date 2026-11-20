-- 存量库迁移：Telegram 转发链溯源（可重复执行）
-- 保留 fwd_from 原生元数据，用于上游母频发现与审核原证溯源
ALTER TABLE raw_messages ADD COLUMN IF NOT EXISTS fwd_from_json JSONB DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_raw_messages_fwd_from_id
    ON raw_messages ((fwd_from_json->>'from_id')) WHERE fwd_from_json IS NOT NULL;

DROP FUNCTION IF EXISTS ingest_append_raw(BIGINT, BIGINT, BIGINT, BIGINT, TEXT, JSONB, TIMESTAMPTZ);
CREATE OR REPLACE FUNCTION ingest_append_raw(
    p_source_id BIGINT,
    p_platform_msg_id BIGINT,
    p_sender_peer_id BIGINT,
    p_grouped_id BIGINT,
    p_raw_text TEXT,
    p_media_json JSONB,
    p_posted_at TIMESTAMPTZ,
    p_fwd_from_json JSONB DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO raw_messages (
        source_id, platform_msg_id, sender_peer_id,
        grouped_id, raw_text, media_json, fwd_from_json, posted_at
    ) VALUES (
        p_source_id, p_platform_msg_id, p_sender_peer_id,
        p_grouped_id, p_raw_text, p_media_json, p_fwd_from_json, p_posted_at
    )
    ON CONFLICT (source_id, platform_msg_id) DO NOTHING
    RETURNING id INTO v_id;
    IF v_id IS NULL THEN
        SELECT id INTO v_id
        FROM raw_messages
        WHERE source_id = p_source_id AND platform_msg_id = p_platform_msg_id;
    END IF;
    RETURN v_id;
END
$fn$;

DROP FUNCTION IF EXISTS ingest_append_sample(BIGINT, BIGINT, BIGINT, BIGINT, TEXT, TIMESTAMPTZ, JSONB);
CREATE OR REPLACE FUNCTION ingest_append_sample(
    p_source_id BIGINT,
    p_platform_msg_id BIGINT,
    p_sender_peer_id BIGINT,
    p_grouped_id BIGINT,
    p_raw_text TEXT,
    p_posted_at TIMESTAMPTZ,
    p_media_json JSONB,
    p_fwd_from_json JSONB DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO raw_messages (
        source_id, platform_msg_id, sender_peer_id, grouped_id,
        raw_text, posted_at, media_json, fwd_from_json, extract_state
    ) VALUES (
        p_source_id, p_platform_msg_id, p_sender_peer_id, p_grouped_id,
        p_raw_text, p_posted_at, p_media_json, p_fwd_from_json, 'new'
    )
    ON CONFLICT (source_id, platform_msg_id) DO NOTHING
    RETURNING id INTO v_id;
    IF v_id IS NULL THEN
        SELECT id INTO v_id
        FROM raw_messages
        WHERE source_id = p_source_id AND platform_msg_id = p_platform_msg_id;
    END IF;
    RETURN v_id;
END
$fn$;
