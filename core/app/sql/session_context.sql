SELECT s.id AS session_id, s.listing_id, s.status,
       l.nickname, l.district, l.price_amount, l.price_currency
FROM chat_sessions s
LEFT JOIN listings l ON l.id = s.listing_id
WHERE s.id = $1::bigint AND s.user_id = $2::bigint;
