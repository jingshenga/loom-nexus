WITH favorite_people AS (
    SELECT DISTINCT COALESCE('g:' || pgm.group_id::text, 'l:' || l.id::text) AS person_key
    FROM user_favorites f
    JOIN listings l ON l.id = f.listing_id AND l.status = 'published'
    LEFT JOIN listing_person_group_members pgm ON pgm.listing_id = l.id
    WHERE f.user_id = $1::bigint
), ranked AS (
    SELECT l.*, rm.source_id, rm.posted_at, cp.is_online, cp.status_tier,
           ROW_NUMBER() OVER (
               PARTITION BY COALESCE('g:' || pgm.group_id::text, 'l:' || l.id::text)
               ORDER BY
                   (l.id=g.primary_listing_id) DESC NULLS LAST,
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
    LEFT JOIN contact_presence cp ON cp.contact_peer_id = l.contact_peer_id
    LEFT JOIN listing_person_group_members pgm ON pgm.listing_id = l.id
    LEFT JOIN listing_person_groups g ON g.id=pgm.group_id
    WHERE l.status = 'published'
      AND COALESCE('g:' || pgm.group_id::text, 'l:' || l.id::text)
          IN (SELECT person_key FROM favorite_people)
)
SELECT r.id, r.source_id, r.nickname, r.district, r.price_amount, r.price_currency, r.bot_url,
       r.description, r.media_json, r.published_at,
       COALESCE(r.is_online, FALSE) AS is_online,
       COALESCE(r.status_tier, 'unknown') AS presence_tier,
       CASE
           WHEN EXISTS (
               SELECT 1 FROM duty_snapshots ds
               WHERE ds.duty_date = ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date
                 AND ds.contact_usernames ? lower(r.contact_username)
           ) THEN 'today'
           WHEN EXISTS (
               SELECT 1 FROM duty_snapshots ds
               WHERE ds.duty_date = ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date - 1
                 AND ds.contact_usernames ? lower(r.contact_username)
           ) THEN 'yesterday'
           ELSE NULL
       END AS duty_label
FROM ranked r
WHERE r.person_rank = 1
ORDER BY (CASE WHEN EXISTS (
             SELECT 1 FROM duty_snapshots ds
             WHERE ds.duty_date = ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date
               AND ds.contact_usernames ? lower(r.contact_username)
         ) THEN 0 ELSE 1 END),
         r.created_at DESC, r.id DESC;
