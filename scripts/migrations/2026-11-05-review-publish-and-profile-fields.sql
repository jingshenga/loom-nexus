CREATE OR REPLACE FUNCTION listing_meets_publish_requirements(
    p_nickname TEXT, p_price NUMERIC, p_description TEXT, p_username TEXT,
    p_bot_url TEXT, p_media JSONB, p_custom_fields JSONB
) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $fn$
    SELECT NULLIF(BTRIM(p_nickname), '') IS NOT NULL
       AND p_price IS NOT NULL
       AND NULLIF(BTRIM(p_description), '') IS NOT NULL
       AND NULLIF(BTRIM(p_username), '') IS NOT NULL
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

UPDATE ingest_rules
SET config_json = jsonb_set(
        COALESCE(config_json, '{}'::jsonb),
        '{extract,district}',
        CASE WHEN rule_name = 'TG群'
             THEN to_jsonb(ARRAY['(?:位置|地区|区域|城区)[:：]\s*#?([^\s,，|｜\n]+)'])
             ELSE COALESCE(config_json #> '{extract,district}', '[]'::jsonb)
                  || to_jsonb(ARRAY['(?:位置|地区|区域|城区)[:：]\s*#?([^\s,，|｜\n]+)'])
        END,
        TRUE
    ),
    version = version + 1
WHERE rule_name IN ('TG群', 'default-rule')
  AND rule_type = 'listing';
