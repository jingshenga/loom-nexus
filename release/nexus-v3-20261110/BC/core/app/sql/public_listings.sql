WITH clock AS (
    SELECT ((NOW() AT TIME ZONE 'Asia/Shanghai' - INTERVAL '4 hours')::date
            + TIME '04:00') AT TIME ZONE 'Asia/Shanghai' AS business_start,
           ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date AS business_date
), filtered AS (
    SELECT l.id, rm.source_id, l.nickname, l.district, l.price_amount, l.price_currency, l.bot_url,
           l.description, l.media_json, l.confidence, l.published_at, l.created_at,
           rm.posted_at,
           pgm.group_id AS person_group_id,
           COALESCE(cp.is_online, FALSE) AS is_online,
           COALESCE(cp.status_tier, 'unknown') AS presence_tier,
           CASE WHEN $9::text = 'yesterday' THEN 'yesterday'
                WHEN $9::text = 'today' THEN 'today'
                WHEN EXISTS (
                    SELECT 1 FROM duty_snapshots ds
                    WHERE ds.duty_date = c.business_date
                      AND ds.contact_usernames ? lower(l.contact_username)
                ) THEN 'today'
                WHEN EXISTS (
                    SELECT 1 FROM duty_snapshots ds
                    WHERE ds.duty_date = c.business_date - 1
                      AND ds.contact_usernames ? lower(l.contact_username)
                ) THEN 'yesterday'
                ELSE NULL END AS duty_label,
           EXISTS (
               SELECT 1 FROM duty_snapshots ds
               WHERE ds.duty_date = c.business_date
                 AND ds.contact_usernames ? lower(l.contact_username)
           ) AS on_duty_today,
           l.created_at >= c.business_start AS newly_collected,
           EXISTS (SELECT 1 FROM user_favorites f
                   WHERE f.listing_id = l.id AND f.user_id = $8::bigint) AS favorited
    FROM listings l
    LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
    LEFT JOIN contact_presence cp ON cp.contact_peer_id = l.contact_peer_id
    LEFT JOIN listing_person_group_members pgm ON pgm.listing_id = l.id
    CROSS JOIN clock c
    WHERE l.status = 'published'
      AND ($1::text IS NULL OR l.nickname ILIKE '%' || $1 || '%'
           OR l.description ILIKE '%' || $1 || '%')
      AND ($2::text IS NULL OR l.district = $2)
      AND ($3::numeric IS NULL OR l.price_amount >= $3)
      AND ($4::numeric IS NULL OR l.price_amount <= $4)
      AND ($7::boolean IS NULL OR COALESCE(cp.is_online, FALSE) = $7)
      AND ($9::text = 'all' OR EXISTS (
          SELECT 1 FROM duty_snapshots ds
          WHERE ds.duty_date = c.business_date
              - CASE WHEN $9::text = 'yesterday' THEN 1 ELSE 0 END
            AND ds.contact_usernames ? lower(l.contact_username)
      ))
), ranked AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY COALESCE('g:' || person_group_id::text, 'l:' || id::text)
        ORDER BY
            on_duty_today DESC,
            (SELECT count(*) FROM jsonb_array_elements(COALESCE(media_json, '[]'::jsonb)) m
             WHERE m->>'kind' IN ('photo','video')
               AND COALESCE(m->>'path',m->>'poster','') <> '') DESC,
            (COALESCE(NULLIF(BTRIM(description), ''), '') <> '') DESC,
            (district IS NOT NULL) DESC,
            (price_amount IS NOT NULL) DESC,
            (bot_url IS NOT NULL AND bot_url <> '') DESC,
            posted_at DESC NULLS LAST, created_at DESC, id DESC
    ) AS person_rank
    FROM filtered
)
SELECT id, source_id, nickname, district, price_amount, price_currency,
       description, media_json, confidence, published_at, is_online, bot_url,
       presence_tier, duty_label, favorited
FROM ranked
WHERE person_rank = 1
ORDER BY (on_duty_today AND newly_collected) DESC,
         on_duty_today DESC, newly_collected DESC, created_at DESC, id DESC
LIMIT GREATEST(1, $5::int) OFFSET GREATEST(0, $6::int);
