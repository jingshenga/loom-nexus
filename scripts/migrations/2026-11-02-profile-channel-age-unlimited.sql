-- Allow associated-channel deep collection regardless of channel age.
-- Only remove the obsolete rule option; preserve jobs, cards, and OCR records.
UPDATE ingest_rules
SET config_json = config_json - 'stale_channel_days'
WHERE rule_type = 'profile_collection'
  AND config_json ? 'stale_channel_days';

ALTER TABLE listings ADD COLUMN IF NOT EXISTS bot_url TEXT NOT NULL DEFAULT '';
ALTER TABLE listings ADD COLUMN IF NOT EXISTS custom_fields JSONB NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS listing_field_definitions (
    field_key VARCHAR(48) PRIMARY KEY,
    label VARCHAR(96) NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]',
    extraction_source VARCHAR(24) NOT NULL DEFAULT 'manual',
    required_for_publish BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_collection_results (
    job_id BIGINT PRIMARY KEY REFERENCES profile_collection_jobs(id) ON DELETE CASCADE,
    contact_username VARCHAR(128) NOT NULL,
    profile_url TEXT NOT NULL DEFAULT '',
    bot_url TEXT NOT NULL DEFAULT '',
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO ingest_rules (rule_name, rule_type, version, config_json, is_active)
SELECT 'Bot地址采集', 'bot_collection', 1, '{}'::jsonb, TRUE
WHERE NOT EXISTS (SELECT 1 FROM ingest_rules WHERE rule_name = 'Bot地址采集');

INSERT INTO source_rule_bindings (source_id, rule_id, priority)
SELECT b.source_id, bot_rule.id, b.priority
FROM source_rule_bindings b
JOIN ingest_rules profile_rule ON profile_rule.id = b.rule_id
                              AND profile_rule.rule_type = 'profile_collection'
JOIN ingest_rules bot_rule ON bot_rule.rule_name = 'Bot地址采集'
                           AND bot_rule.rule_type = 'bot_collection'
ON CONFLICT (source_id, rule_id) DO NOTHING;
