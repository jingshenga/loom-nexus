SELECT l.id, rm.source_id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.media_json, l.confidence, l.published_at,
       COALESCE(cp.is_online, FALSE) AS is_online,
       COALESCE(cp.status_tier, 'unknown') AS presence_tier,
       EXISTS (SELECT 1 FROM user_favorites f
               WHERE f.listing_id = l.id AND f.user_id = $8::bigint) AS favorited
FROM listings l
JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN contact_presence cp ON cp.contact_peer_id = l.contact_peer_id
WHERE l.status = 'published'
  AND ($1::text IS NULL OR l.nickname ILIKE '%' || $1 || '%'
       OR l.description ILIKE '%' || $1 || '%')
  AND ($2::text IS NULL OR l.district = $2)
  AND ($3::numeric IS NULL OR l.price_amount >= $3)
  AND ($4::numeric IS NULL OR l.price_amount <= $4)
  AND ($7::boolean IS NULL OR COALESCE(cp.is_online, FALSE) = $7)
ORDER BY l.published_at DESC
LIMIT GREATEST(1, $5::int) OFFSET GREATEST(0, $6::int);
