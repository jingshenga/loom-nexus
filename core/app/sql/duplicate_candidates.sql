WITH source_cards AS (
    SELECT l.id, l.status, l.contact_peer_id, l.contact_username,
           l.nickname, l.district, l.price_amount, l.price_currency,
           l.description, l.media_json, l.created_at, rm.raw_text,
           rm.posted_at, s.title AS source_title,
           regexp_replace(
               regexp_replace(
                   lower(coalesce(nullif(rm.raw_text, ''), l.description, '')),
                   'https?://[^[:space:]]+', ' ', 'g'
               ),
               '@[[:alnum:]_]+', ' ', 'g'
           ) AS normalized_text
    FROM listings l
    JOIN raw_messages rm ON rm.id = l.source_msg_id
    LEFT JOIN ingest_sources s ON s.id = rm.source_id
    WHERE l.status IN ('published', 'pending')
), normalized AS (
    SELECT *, regexp_replace(normalized_text, '[[:space:]]+', '', 'g')
                  AS content_signature
    FROM source_cards
)
SELECT a.id AS left_id, a.status AS left_status,
       a.contact_peer_id AS left_peer_id, a.contact_username AS left_username,
       a.nickname AS left_nickname, a.district AS left_district,
       a.price_amount AS left_price, a.price_currency AS left_currency,
       a.description AS left_description, a.media_json AS left_media,
       a.raw_text AS left_source_text, a.source_title AS left_source_title,
       a.created_at AS left_collected_at, a.posted_at AS left_posted_at,
       b.id AS right_id, b.status AS right_status,
       b.contact_peer_id AS right_peer_id, b.contact_username AS right_username,
       b.nickname AS right_nickname, b.district AS right_district,
       b.price_amount AS right_price, b.price_currency AS right_currency,
       b.description AS right_description, b.media_json AS right_media,
       b.raw_text AS right_source_text, b.source_title AS right_source_title,
       b.created_at AS right_collected_at, b.posted_at AS right_posted_at
FROM normalized a
JOIN normalized b ON a.content_signature = b.content_signature AND a.id < b.id
LEFT JOIN listing_duplicate_decisions d
       ON d.left_listing_id = a.id AND d.right_listing_id = b.id
WHERE a.content_signature <> ''
  AND a.nickname IS NOT NULL
  AND lower(trim(a.nickname)) = lower(trim(b.nickname))
  AND a.district IS NOT DISTINCT FROM b.district
  AND a.price_amount IS NOT DISTINCT FROM b.price_amount
  AND a.price_currency IS NOT DISTINCT FROM b.price_currency
  AND (
      (a.contact_peer_id IS NOT NULL AND b.contact_peer_id IS NOT NULL
       AND a.contact_peer_id <> b.contact_peer_id)
      OR ((a.contact_peer_id IS NULL OR b.contact_peer_id IS NULL)
          AND lower(coalesce(a.contact_username, ''))
              <> lower(coalesce(b.contact_username, '')))
  )
  AND d.left_listing_id IS NULL
ORDER BY GREATEST(a.created_at, b.created_at) DESC, a.id, b.id;
