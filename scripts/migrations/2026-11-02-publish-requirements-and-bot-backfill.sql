CREATE OR REPLACE FUNCTION listing_meets_publish_requirements(
    p_nickname TEXT, p_price NUMERIC, p_description TEXT, p_username TEXT,
    p_bot_url TEXT, p_media JSONB, p_custom_fields JSONB
) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $fn$
    SELECT NULLIF(BTRIM(p_nickname), '') IS NOT NULL
       AND p_price IS NOT NULL
       AND NULLIF(BTRIM(p_description), '') IS NOT NULL
       AND NULLIF(BTRIM(p_username), '') IS NOT NULL
       AND NULLIF(BTRIM(p_bot_url), '') IS NOT NULL
       AND (SELECT count(*) FROM jsonb_array_elements(COALESCE(p_media, '[]'::jsonb)) item
            WHERE item->>'kind' IN ('photo','video')
              AND COALESCE(item->>'path',item->>'poster','') <> ''
              AND COALESCE(item->>'visibility','public') = 'public'
              AND COALESCE(item->>'ocr_classification','') NOT IN ('info_card','uncertain')
              AND COALESCE(item->>'ocr_review_status','not_required') <> 'pending') >= 3
       AND NOT EXISTS (
            SELECT 1 FROM listing_field_definitions d
            WHERE d.is_active AND d.required_for_publish
              AND COALESCE(NULLIF(BTRIM(p_custom_fields->>d.field_key), ''), '') = ''
       )
$fn$;

WITH candidates AS (
    SELECT DISTINCT ON (r.id, lower(l.contact_username))
           r.id AS rule_id, s.id AS source_id, s.assigned_account_id AS account_id,
           lower(l.contact_username) AS contact_username
    FROM listings l
    LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
    LEFT JOIN profile_collection_jobs previous
           ON previous.id = l.profile_collection_job_id
    JOIN ingest_sources s
      ON s.id = COALESCE(rm.source_id, previous.source_id)
     AND s.is_active
    JOIN bridge_accounts a ON a.id = s.assigned_account_id AND a.status = 'active'
    JOIN source_rule_bindings b ON b.source_id = s.id
    JOIN ingest_rules r ON r.id = b.rule_id
                       AND r.rule_type = 'bot_collection' AND r.is_active
    WHERE l.status = 'published'
      AND NULLIF(BTRIM(l.contact_username), '') IS NOT NULL
      AND NULLIF(BTRIM(l.bot_url), '') IS NULL
    ORDER BY r.id, lower(l.contact_username), b.priority DESC, s.id
)
INSERT INTO profile_collection_jobs
    (rule_id, duty_date, source_id, account_id, contact_username)
SELECT rule_id, (NOW() AT TIME ZONE 'Asia/Shanghai')::date,
       source_id, account_id, contact_username
FROM candidates
ON CONFLICT (rule_id, duty_date, lower(contact_username)) WHERE target_msg_id IS NULL
DO NOTHING;
