SELECT count(*) AS total
FROM (
SELECT l.id,
       ROW_NUMBER() OVER (
           PARTITION BY COALESCE('g:' || pgm.group_id::text, 'l:' || l.id::text)
           ORDER BY
               (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m
                WHERE m->>'kind' IN ('photo','video')
                  AND COALESCE(m->>'path',m->>'poster','') <> '') DESC,
               (COALESCE(NULLIF(BTRIM(l.description), ''), '') <> '') DESC,
               (l.district IS NOT NULL) DESC,
               (l.price_amount IS NOT NULL) DESC,
               (l.bot_url IS NOT NULL AND l.bot_url <> '') DESC,
               rm.posted_at DESC NULLS LAST, l.created_at DESC, l.id DESC
       ) AS person_rank
FROM listings l
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN listing_person_group_members pgm ON pgm.listing_id = l.id
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
  ))
) people
WHERE person_rank = 1;
