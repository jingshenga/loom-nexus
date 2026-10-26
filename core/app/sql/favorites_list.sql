SELECT l.id, rm.source_id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.media_json, l.published_at,
       COALESCE(cp.is_online, FALSE) AS is_online,
       COALESCE(cp.status_tier, 'unknown') AS presence_tier
FROM user_favorites f
JOIN listings l ON l.id = f.listing_id AND l.status = 'published'
JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN contact_presence cp ON cp.contact_peer_id = l.contact_peer_id
WHERE f.user_id = $1::bigint
ORDER BY f.created_at DESC;
