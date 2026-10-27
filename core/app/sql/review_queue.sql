SELECT l.id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.media_json, l.evidence_json, l.confidence, l.status,
       l.contact_peer_id, l.contact_username, l.created_at,
       rm.raw_text, s.title AS source_title, rm.posted_at AS raw_posted_at
FROM listings l
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN ingest_sources s ON s.id = rm.source_id
WHERE l.status = $1::text
  AND ($4::text IS NULL OR l.nickname ILIKE '%' || $4::text || '%')
ORDER BY l.id DESC
LIMIT GREATEST(1, $2::int) OFFSET GREATEST(0, $3::int);
