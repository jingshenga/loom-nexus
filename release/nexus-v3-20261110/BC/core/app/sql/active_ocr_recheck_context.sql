SELECT r.id AS raw_id,r.source_id,r.platform_msg_id,r.sender_peer_id,r.grouped_id,
       r.raw_text,r.media_json,r.posted_at,r.extract_state,
       rev.id AS revision_id,rev.content_hash,
       COALESCE((SELECT MAX(e.id) FROM rule_router_mode_events e WHERE e.source_id=r.source_id),0)
           AS mode_event_id
FROM raw_messages r
JOIN LATERAL (
    SELECT id,content_hash FROM raw_message_revisions
    WHERE raw_id=r.id ORDER BY revision DESC LIMIT 1
) rev ON TRUE
WHERE r.id=$1;
