ALTER TABLE duty_snapshots
    ADD COLUMN IF NOT EXISTS green_only_usernames JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS green_blue_usernames JSONB NOT NULL DEFAULT '[]';

DROP FUNCTION IF EXISTS duty_snapshot_upsert(
    BIGINT, BIGINT, DATE, BIGINT, VARCHAR, JSONB, JSONB, TIMESTAMPTZ, TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION duty_snapshot_upsert(
    p_rule_id BIGINT,
    p_source_id BIGINT,
    p_duty_date DATE,
    p_platform_msg_id BIGINT,
    p_content_hash VARCHAR(64),
    p_contact_usernames JSONB,
    p_unresolved_links JSONB,
    p_green_only_usernames JSONB,
    p_green_blue_usernames JSONB,
    p_observed_at TIMESTAMPTZ,
    p_expires_at TIMESTAMPTZ
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE
    v_changed BOOLEAN := FALSE;
BEGIN
    INSERT INTO duty_snapshots AS ds (
        rule_id, source_id, duty_date, platform_msg_id, content_hash,
        contact_usernames, unresolved_links, green_only_usernames,
        green_blue_usernames, observed_at, expires_at
    ) VALUES (
        p_rule_id, p_source_id, p_duty_date, p_platform_msg_id, p_content_hash,
        p_contact_usernames, p_unresolved_links, p_green_only_usernames,
        p_green_blue_usernames, p_observed_at, p_expires_at
    )
    ON CONFLICT (rule_id, source_id, duty_date) DO UPDATE SET
        platform_msg_id = EXCLUDED.platform_msg_id,
        content_hash = EXCLUDED.content_hash,
        contact_usernames = CASE
            WHEN jsonb_array_length(EXCLUDED.contact_usernames) = 0
             AND jsonb_array_length(EXCLUDED.unresolved_links) > 0
                THEN ds.contact_usernames
            ELSE EXCLUDED.contact_usernames
        END,
        green_only_usernames = CASE
            WHEN jsonb_array_length(EXCLUDED.contact_usernames) = 0
             AND jsonb_array_length(EXCLUDED.unresolved_links) > 0
                THEN ds.green_only_usernames
            ELSE EXCLUDED.green_only_usernames
        END,
        green_blue_usernames = CASE
            WHEN jsonb_array_length(EXCLUDED.contact_usernames) = 0
             AND jsonb_array_length(EXCLUDED.unresolved_links) > 0
                THEN ds.green_blue_usernames
            ELSE EXCLUDED.green_blue_usernames
        END,
        unresolved_links = EXCLUDED.unresolved_links,
        observed_at = EXCLUDED.observed_at,
        expires_at = EXCLUDED.expires_at
    WHERE ds.content_hash IS DISTINCT FROM EXCLUDED.content_hash
       OR ds.platform_msg_id IS DISTINCT FROM EXCLUDED.platform_msg_id
    RETURNING TRUE INTO v_changed;
    RETURN COALESCE(v_changed, FALSE);
END
$fn$;

UPDATE ingest_rules
SET config_json = config_json || '{"duty_identity_markers":true}'::jsonb,
    version = version + 1
WHERE rule_name = '天一今日签到' AND rule_type = 'on_duty'
  AND config_json->>'duty_identity_markers' IS DISTINCT FROM 'true';

UPDATE ingest_rules
SET config_json = config_json || '{"target_peer_id":-1003083173117}'::jsonb,
    version = version + 1
WHERE rule_name = '小木屋今日签到' AND rule_type = 'on_duty'
  AND config_json->>'target_peer_id' IS DISTINCT FROM '-1003083173117';
