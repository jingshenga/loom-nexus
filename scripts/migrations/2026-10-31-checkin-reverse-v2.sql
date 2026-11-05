-- Separate manual TG lookup from reverse lookup of daily check-in accounts.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS profile_collection_job_id BIGINT
    REFERENCES profile_collection_jobs(id) ON DELETE SET NULL;
ALTER TABLE profile_collection_jobs
    ADD COLUMN IF NOT EXISTS stage VARCHAR(32) NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS progress_current INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS progress_total INT,
    ADD COLUMN IF NOT EXISTS progress_note VARCHAR(240),
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_profile_collection_job
    ON listings(profile_collection_job_id)
    WHERE profile_collection_job_id IS NOT NULL;

UPDATE ingest_rules
SET rule_name = 'TG号采集V2'
WHERE rule_name = '关联群组资料深采 V2'
  AND NOT EXISTS (SELECT 1 FROM ingest_rules WHERE rule_name = 'TG号采集V2');

INSERT INTO ingest_rules (rule_name, rule_type, version, config_json, is_active)
VALUES ('TG号采集V2', 'profile_collection', 1,
        '{"recent_window":10,"max_scan_messages":100,"max_photos":6,"max_videos":2,"intro_min_chars":30,"intro_min_lines":3,"channel_any_sender_matches":true,"skip_video_when_photo_found":true,"exclude_avatar":true,"stale_channel_days":60,"pinned_followup_messages":5}'::jsonb,
        TRUE)
ON CONFLICT (rule_name) DO NOTHING;

INSERT INTO ingest_rules (rule_name, rule_type, version, config_json, is_active)
VALUES
    ('天一签到来源反查 V2', 'checkin_reverse', 1,
     '{"collection_mode":"checkin_source_reverse","source_username":"nbtgzb"}'::jsonb, TRUE),
    ('小木屋签到来源反查 V2', 'checkin_reverse', 1,
     '{"collection_mode":"checkin_source_reverse","source_username":"NBXMWBGJ"}'::jsonb, TRUE)
ON CONFLICT (rule_name) DO UPDATE
SET rule_type = EXCLUDED.rule_type,
    config_json = EXCLUDED.config_json,
    is_active = TRUE;

INSERT INTO source_rule_bindings (source_id, rule_id, priority)
SELECT s.id, reverse_rule.id, 100
FROM (VALUES
    ('天一今日签到'::varchar, '天一签到来源反查 V2'::varchar),
    ('小木屋今日签到'::varchar, '小木屋签到来源反查 V2'::varchar)
) AS wanted(duty_rule_name, reverse_rule_name)
JOIN ingest_rules duty_rule ON duty_rule.rule_name = wanted.duty_rule_name
                           AND duty_rule.rule_type = 'on_duty' AND duty_rule.is_active
JOIN ingest_sources s ON s.id = (duty_rule.config_json->>'collection_source_id')::bigint
                     AND s.is_active AND s.assigned_account_id IS NOT NULL
JOIN ingest_rules reverse_rule ON reverse_rule.rule_name = wanted.reverse_rule_name
                              AND reverse_rule.is_active
ON CONFLICT (source_id, rule_id) DO NOTHING;
