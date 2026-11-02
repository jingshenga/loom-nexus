SELECT rm.id, rm.platform_msg_id, rm.source_id, s.title AS source_title,
       s.username AS source_username, rm.raw_text, rm.media_json,
       rm.posted_at, rm.extract_state,
       l.id AS listing_id, l.status AS listing_status,
       l.nickname, l.contact_username, l.created_at AS listing_created_at
FROM raw_messages rm
JOIN checkin_history_scans hs ON hs.source_id = rm.source_id
JOIN ingest_sources s ON s.id = rm.source_id
LEFT JOIN LATERAL (
    SELECT l.* FROM listings l WHERE l.source_msg_id = rm.id
    ORDER BY l.id DESC LIMIT 1
) l ON TRUE
WHERE ($1::bigint IS NULL OR rm.source_id = $1::bigint)
  AND ($2::text IS NULL OR COALESCE(rm.raw_text, '') ILIKE '%' || $2::text || '%'
       OR COALESCE(l.nickname, '') ILIKE '%' || $2::text || '%'
       OR COALESCE(l.contact_username, '') ILIKE '%' || $2::text || '%')
ORDER BY rm.posted_at DESC NULLS LAST, rm.id DESC
LIMIT $3::int OFFSET $4::int;
