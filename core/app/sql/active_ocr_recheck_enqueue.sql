INSERT INTO rule_active_ocr_rechecks(
    source_id,raw_id,raw_revision_id,content_hash,mode_event_id,rule_version_ids
)
SELECT r.source_id,r.id,rev.id,rev.content_hash,
       COALESCE((SELECT MAX(e.id) FROM rule_router_mode_events e WHERE e.source_id=r.source_id),0),
       $3::bigint[]
FROM raw_messages r
JOIN LATERAL (
    SELECT id,content_hash FROM raw_message_revisions
    WHERE raw_id=r.id ORDER BY revision DESC LIMIT 1
) rev ON TRUE
WHERE r.id=$1 AND r.source_id=$2
ON CONFLICT(source_id,raw_id,raw_revision_id) DO NOTHING;
