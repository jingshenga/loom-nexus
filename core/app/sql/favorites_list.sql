SELECT l.id, rm.source_id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.media_json, l.published_at
FROM user_favorites f
JOIN listings l ON l.id = f.listing_id AND l.status = 'published'
JOIN raw_messages rm ON rm.id = l.source_msg_id
WHERE f.user_id = $1::bigint
ORDER BY f.created_at DESC;
