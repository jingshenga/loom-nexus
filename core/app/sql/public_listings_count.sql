SELECT count(*) AS total
FROM listings l
LEFT JOIN contact_presence cp ON cp.contact_peer_id = l.contact_peer_id
WHERE l.status = 'published'
  AND ($1::text IS NULL OR l.nickname ILIKE '%' || $1 || '%'
       OR l.description ILIKE '%' || $1 || '%')
  AND ($2::text IS NULL OR l.district = $2)
  AND ($3::numeric IS NULL OR l.price_amount >= $3)
  AND ($4::numeric IS NULL OR l.price_amount <= $4)
  AND ($5::boolean IS NULL OR COALESCE(cp.is_online, FALSE) = $5)
  AND ($6::text = 'all' OR EXISTS (
      SELECT 1 FROM duty_snapshots ds
      WHERE ds.duty_date = (
          ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date
          - CASE WHEN $6::text = 'yesterday' THEN 1 ELSE 0 END
      )
        AND ds.contact_usernames ? lower(l.contact_username)
  ));
