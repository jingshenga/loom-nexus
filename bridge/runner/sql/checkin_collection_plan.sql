SELECT CASE WHEN EXISTS (
    SELECT 1 FROM app_settings WHERE key = 'collection_reuse_enabled' AND value = '1'
) THEN nexus_collection_plan($1::bigint, $2::bigint, $3::bigint, $4::text) END AS plan;
