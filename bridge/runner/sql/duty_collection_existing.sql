SELECT id, extract_state
FROM raw_messages
WHERE source_id = $1::bigint
  AND (platform_msg_id = $2::bigint
       OR ($3::bigint IS NOT NULL AND grouped_id = $3::bigint))
ORDER BY (platform_msg_id = $2::bigint) DESC
LIMIT 1;
