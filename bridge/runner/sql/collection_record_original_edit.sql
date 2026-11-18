UPDATE raw_messages SET raw_text=$3::text
WHERE source_id=$1::bigint AND platform_msg_id=$2::bigint
    AND extract_state IS DISTINCT FROM 'processing'
    AND COALESCE(raw_text,'') IS DISTINCT FROM $3::text
    AND EXISTS (SELECT 1 FROM app_settings WHERE key='collection_reuse_enabled' AND value='1');
