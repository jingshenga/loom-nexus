SELECT id, platform_msg_id FROM raw_messages
WHERE source_id = $1::bigint
ORDER BY id DESC
LIMIT GREATEST(1, $2::int);
