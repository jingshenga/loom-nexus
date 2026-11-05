SELECT l.contact_username, l.nickname
FROM ingest_sources s
JOIN raw_messages rm ON rm.source_id = s.id
JOIN listings l ON l.source_msg_id = rm.id
WHERE s.peer_id = $1::bigint
  AND rm.platform_msg_id = $2::bigint
  AND l.contact_username IS NOT NULL
ORDER BY (l.status = 'published') DESC, l.updated_at DESC
LIMIT 1;
