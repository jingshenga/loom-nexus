ALTER TABLE profile_collection_jobs ALTER COLUMN target_peer_id DROP NOT NULL;

UPDATE ingest_rules
SET config_json = config_json || '{"stale_channel_days":60,"pinned_followup_messages":5}'::jsonb
WHERE rule_type = 'profile_collection';
