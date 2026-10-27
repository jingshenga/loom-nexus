CREATE TABLE IF NOT EXISTS duty_collection_jobs (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES ingest_rules(id) ON DELETE CASCADE,
    duty_date DATE NOT NULL,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES bridge_accounts(id),
    contact_username VARCHAR(128) NOT NULL,
    target_peer_id BIGINT NOT NULL,
    target_msg_id BIGINT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    raw_id BIGINT REFERENCES raw_messages(id) ON DELETE SET NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_duty_collection_jobs_identity
    ON duty_collection_jobs(duty_date, lower(contact_username));
CREATE INDEX IF NOT EXISTS idx_duty_collection_jobs_pending
    ON duty_collection_jobs(status, id);

WITH mappings(rule_name, source_title) AS (
    VALUES
        ('天一今日在岗', '宁波TG老师专榜'),
        ('小木屋今日在岗', '宁波小木屋分享榜')
)
UPDATE ingest_rules ir
SET config_json = ir.config_json || jsonb_build_object(
    'auto_collect_enabled', TRUE,
    'collection_source_id', s.id
)
FROM mappings m
JOIN ingest_sources s ON s.title = m.source_title AND s.is_active
WHERE ir.rule_name = m.rule_name
  AND ir.rule_type = 'on_duty'
  AND EXISTS (
      SELECT 1 FROM source_rule_bindings b
      JOIN ingest_rules listing_rule ON listing_rule.id = b.rule_id
      WHERE b.source_id = s.id AND listing_rule.is_active
        AND listing_rule.rule_type = 'listing'
  );
