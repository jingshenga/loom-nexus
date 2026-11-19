SELECT l.id, rm.source_id, l.nickname, l.district, l.price_amount, l.price_currency, l.bot_url,
       l.description, l.custom_fields, l.evidence_json, l.media_json, l.contact_username, l.confidence,
       l.published_at, COALESCE(cp.is_online, FALSE) AS is_online,
       COALESCE(cp.status_tier, 'unknown') AS presence_tier,
       CASE
         WHEN EXISTS (
             SELECT 1 FROM duty_snapshots ds
             WHERE ds.duty_date = ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date
               AND ds.contact_usernames ? lower(l.contact_username)
         ) THEN 'today'
         WHEN EXISTS (
             SELECT 1 FROM duty_snapshots ds
             WHERE ds.duty_date = ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date - 1
               AND ds.contact_usernames ? lower(l.contact_username)
         ) THEN 'yesterday'
         ELSE NULL
       END AS duty_label
FROM listings l
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN contact_presence cp ON cp.contact_peer_id = l.contact_peer_id
WHERE l.id = $1::bigint AND l.status = 'published';
