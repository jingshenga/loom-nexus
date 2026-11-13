WITH cards AS (
    SELECT l.id, l.source_msg_id, l.status, l.contact_peer_id, l.contact_username,
           l.nickname, l.district, l.price_amount, l.price_currency,
           l.description, l.media_json, l.created_at, rm.raw_text,
           rm.posted_at, s.title AS source_title,
           l.profile_collection_job_id, pgm.group_id AS person_group_id,
           regexp_replace(
               regexp_replace(lower(coalesce(nullif(rm.raw_text, ''), l.description, '')),
                              'https?://[^[:space:]]+', ' ', 'g'),
               '@[[:alnum:]_]+', ' ', 'g'
           ) AS normalized_text
    FROM listings l
    LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
    LEFT JOIN ingest_sources s ON s.id = rm.source_id
    LEFT JOIN listing_person_group_members pgm ON pgm.listing_id = l.id
    WHERE l.status IN ('published', 'pending', 'duplicate_pending', 'archived')
), normalized AS (
    SELECT *, regexp_replace(normalized_text, '[[:space:]]+', '', 'g') AS content_signature
    FROM cards
), candidates AS (
SELECT a.id AS left_id, a.status AS left_status,
       a.contact_peer_id AS left_peer_id, a.contact_username AS left_username,
       a.nickname AS left_nickname, a.district AS left_district,
       a.price_amount AS left_price, a.price_currency AS left_currency,
       a.description AS left_description, a.media_json AS left_media,
       COALESCE(a.raw_text, a.description) AS left_source_text,
       a.source_title AS left_source_title,
       a.created_at AS left_collected_at, a.posted_at AS left_posted_at,
       b.id AS right_id, b.status AS right_status,
       b.contact_peer_id AS right_peer_id, b.contact_username AS right_username,
       b.nickname AS right_nickname, b.district AS right_district,
       b.price_amount AS right_price, b.price_currency AS right_currency,
       b.description AS right_description, b.media_json AS right_media,
       COALESCE(b.raw_text, b.description) AS right_source_text,
       b.source_title AS right_source_title,
       b.created_at AS right_collected_at, b.posted_at AS right_posted_at,
       CASE
           WHEN a.contact_peer_id IS NOT NULL AND a.contact_peer_id = b.contact_peer_id THEN 'TG 数字 ID 一致'
           WHEN NULLIF(a.contact_username, '') IS NOT NULL
                AND lower(a.contact_username) = lower(b.contact_username)
                AND a.contact_peer_id IS NOT NULL AND b.contact_peer_id IS NOT NULL
                AND a.contact_peer_id <> b.contact_peer_id THEN 'TG 号对应数字 ID 冲突'
           WHEN NULLIF(a.contact_username, '') IS NOT NULL
                AND lower(a.contact_username) = lower(b.contact_username)
                AND (a.contact_peer_id IS NULL OR b.contact_peer_id IS NULL
                     OR a.contact_peer_id = b.contact_peer_id) THEN 'TG 号一致'
           WHEN NULLIF(a.nickname, '') IS NOT NULL
                AND regexp_replace(lower(a.nickname), '[[:space:][:punct:]_]+', '', 'g')
                    = regexp_replace(lower(b.nickname), '[[:space:][:punct:]_]+', '', 'g') THEN '昵称一致，待核对'
           ELSE '资料内容相似'
       END AS match_reason,
       ROW_NUMBER() OVER (
           PARTITION BY
               LEAST(COALESCE('g:' || a.person_group_id::text, 'l:' || a.id::text),
                     COALESCE('g:' || b.person_group_id::text, 'l:' || b.id::text)),
               GREATEST(COALESCE('g:' || a.person_group_id::text, 'l:' || a.id::text),
                        COALESCE('g:' || b.person_group_id::text, 'l:' || b.id::text))
           ORDER BY
               ((a.contact_peer_id IS NOT NULL AND a.contact_peer_id = b.contact_peer_id)
                OR (NULLIF(a.contact_username, '') IS NOT NULL
                    AND lower(a.contact_username) = lower(b.contact_username))) DESC,
               GREATEST(a.created_at, b.created_at) DESC, a.id, b.id
       ) AS group_pair_rank
FROM normalized a
JOIN normalized b ON a.id < b.id
LEFT JOIN listing_duplicate_decisions d
       ON d.left_listing_id = a.id AND d.right_listing_id = b.id
WHERE d.left_listing_id IS NULL
  AND (a.person_group_id IS NULL OR b.person_group_id IS NULL OR a.person_group_id <> b.person_group_id)
  AND NOT (NULLIF(a.contact_username, '') IS NOT NULL
           AND lower(a.contact_username) = lower(b.contact_username)
           AND (a.contact_peer_id IS NULL OR b.contact_peer_id IS NULL
                OR a.contact_peer_id = b.contact_peer_id))
  AND (
      ((a.profile_collection_job_id IS NOT NULL OR b.profile_collection_job_id IS NOT NULL
        OR EXISTS (SELECT 1 FROM raw_messages h JOIN checkin_history_scans hs ON hs.source_id=h.source_id
                   WHERE h.id=a.source_msg_id)
        OR EXISTS (SELECT 1 FROM raw_messages h JOIN checkin_history_scans hs ON hs.source_id=h.source_id
                   WHERE h.id=b.source_msg_id))
       AND (
           (a.contact_peer_id IS NOT NULL AND a.contact_peer_id = b.contact_peer_id)
           OR (NULLIF(a.contact_username, '') IS NOT NULL
               AND lower(a.contact_username) = lower(b.contact_username)
               AND (a.contact_peer_id IS NULL OR b.contact_peer_id IS NULL
                    OR a.contact_peer_id = b.contact_peer_id))
           OR (NULLIF(a.nickname, '') IS NOT NULL
               AND regexp_replace(lower(a.nickname), '[[:space:][:punct:]_]+', '', 'g')
                   = regexp_replace(lower(b.nickname), '[[:space:][:punct:]_]+', '', 'g'))
       ))
      OR (a.content_signature = b.content_signature
          AND a.content_signature <> ''
          AND a.nickname IS NOT NULL
          AND regexp_replace(lower(a.nickname), '[[:space:][:punct:]_]+', '', 'g')
              = regexp_replace(lower(b.nickname), '[[:space:][:punct:]_]+', '', 'g')
          AND a.district IS NOT DISTINCT FROM b.district
          AND a.price_amount IS NOT DISTINCT FROM b.price_amount
          AND a.price_currency IS NOT DISTINCT FROM b.price_currency
          AND ((a.contact_peer_id IS NOT NULL AND b.contact_peer_id IS NOT NULL
                AND a.contact_peer_id <> b.contact_peer_id)
               OR ((a.contact_peer_id IS NULL OR b.contact_peer_id IS NULL)
                   AND lower(coalesce(a.contact_username, ''))
                       <> lower(coalesce(b.contact_username, '')))))
  )
)
SELECT * FROM candidates
WHERE group_pair_rank = 1
ORDER BY GREATEST(left_collected_at, right_collected_at) DESC, left_id, right_id;
