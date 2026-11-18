SELECT raw_text,media_json FROM raw_messages
WHERE source_id=$1::bigint AND platform_msg_id=$2::bigint
  AND grouped_id IS NOT DISTINCT FROM $3::bigint
  AND EXISTS (SELECT 1 FROM app_settings WHERE key='collection_reuse_enabled' AND value='1');
