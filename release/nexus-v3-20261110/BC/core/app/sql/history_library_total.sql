SELECT count(*)
FROM raw_messages rm
JOIN checkin_history_scans hs ON hs.source_id = rm.source_id
LEFT JOIN LATERAL (
    SELECT l.nickname, l.contact_username
    FROM listings l WHERE l.source_msg_id = rm.id
    ORDER BY l.id DESC LIMIT 1
) l ON TRUE
WHERE ($1::bigint IS NULL OR rm.source_id = $1::bigint)
  AND ($2::text IS NULL OR COALESCE(rm.raw_text, '') ILIKE '%' || $2::text || '%'
       OR COALESCE(l.nickname, '') ILIKE '%' || $2::text || '%'
       OR COALESCE(l.contact_username, '') ILIKE '%' || $2::text || '%');
