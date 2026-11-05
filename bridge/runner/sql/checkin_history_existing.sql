SELECT platform_msg_id
FROM raw_messages
WHERE source_id = $1::bigint AND platform_msg_id = ANY($2::bigint[]);
