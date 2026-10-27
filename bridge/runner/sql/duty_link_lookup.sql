SELECT contact_username
FROM duty_link_cache
WHERE target_peer_id = $1::bigint AND target_msg_id = $2::bigint;
