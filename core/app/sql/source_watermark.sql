SELECT COALESCE(MIN(platform_msg_id), 0) AS min_msg_id,
       COALESCE(MAX(platform_msg_id), 0) AS max_msg_id,
       COUNT(*)::bigint AS total_ingested
FROM raw_messages
WHERE source_id = $1::bigint;
