SELECT i.listing_id, l.status, i.nickname, i.platform_msg_id, i.posted_at
FROM checkin_identity_index i
JOIN listings l ON l.id = i.listing_id
WHERE i.source_id = $1::bigint
  AND lower(i.contact_username) = lower($2::text)
  AND l.status <> 'rejected'
ORDER BY i.posted_at DESC NULLS LAST, i.collected_at DESC, i.listing_id DESC
LIMIT 1;
