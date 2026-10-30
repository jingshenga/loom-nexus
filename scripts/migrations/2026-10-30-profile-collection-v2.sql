CREATE TABLE IF NOT EXISTS profile_collection_jobs (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES ingest_rules(id) ON DELETE CASCADE,
    duty_date DATE NOT NULL,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES bridge_accounts(id),
    contact_username VARCHAR(128) NOT NULL,
    target_peer_id BIGINT NOT NULL,
    target_msg_id BIGINT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    next_attempt_at TIMESTAMPTZ,
    retry_count INT NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_collection_jobs_identity
    ON profile_collection_jobs(rule_id, duty_date, lower(contact_username));
CREATE INDEX IF NOT EXISTS idx_profile_collection_jobs_due
    ON profile_collection_jobs(status, next_attempt_at, id);

CREATE TABLE IF NOT EXISTS profile_collection_results (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL UNIQUE REFERENCES profile_collection_jobs(id) ON DELETE CASCADE,
    contact_peer_id BIGINT,
    contact_username VARCHAR(128) NOT NULL,
    profile_url TEXT NOT NULL DEFAULT '',
    channel_url TEXT NOT NULL DEFAULT '',
    bot_url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    media_json JSONB NOT NULL DEFAULT '[]',
    evidence_json JSONB NOT NULL DEFAULT '{}',
    profile_config JSONB NOT NULL DEFAULT '{}',
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE listing_media_ocr
    ADD COLUMN IF NOT EXISTS profile_result_id BIGINT
        REFERENCES profile_collection_results(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_media_ocr_profile_result_media
    ON listing_media_ocr(profile_result_id, media_path)
    WHERE profile_result_id IS NOT NULL;

INSERT INTO ingest_rules (rule_name, rule_type, version, config_json, is_active)
SELECT '关联群组资料深采 V2', 'profile_collection', 1,
       '{"recent_window":10,"max_scan_messages":100,"max_photos":6,"max_videos":2,"intro_min_chars":30,"intro_min_lines":3,"channel_any_sender_matches":true,"skip_video_when_photo_found":true,"exclude_avatar":true}'::jsonb,
       TRUE
WHERE NOT EXISTS (
    SELECT 1 FROM ingest_rules WHERE rule_name = '关联群组资料深采 V2'
);

INSERT INTO source_rule_bindings (source_id, rule_id, priority)
SELECT DISTINCT (d.config_json->>'collection_source_id')::bigint, v.id, 100
FROM ingest_rules d
JOIN ingest_rules v ON v.rule_name = '关联群组资料深采 V2'
WHERE d.rule_type = 'on_duty'
  AND d.is_active
  AND COALESCE(d.config_json->>'auto_collect_enabled', 'true') = 'true'
  AND COALESCE(d.config_json->>'collection_source_id', '') ~ '^[0-9]+$'
ON CONFLICT (source_id, rule_id) DO NOTHING;
