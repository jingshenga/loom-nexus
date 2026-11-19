WITH cards AS (
    SELECT l.id, l.source_msg_id, l.status, l.contact_peer_id, l.contact_username,
           l.nickname, l.district, l.price_amount, l.price_currency,
           l.description, l.media_json, l.created_at, rm.raw_text,
           rm.posted_at, s.title AS source_title,
           pgm.group_id AS person_group_id
    FROM listings l
    LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
    LEFT JOIN ingest_sources s ON s.id = rm.source_id
    LEFT JOIN listing_person_group_members pgm ON pgm.listing_id = l.id
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
       conflict.match_reason,
       ROW_NUMBER() OVER (
           PARTITION BY
               LEAST(COALESCE('g:' || a.person_group_id::text, 'l:' || a.id::text),
                     COALESCE('g:' || b.person_group_id::text, 'l:' || b.id::text)),
               GREATEST(COALESCE('g:' || a.person_group_id::text, 'l:' || a.id::text),
                        COALESCE('g:' || b.person_group_id::text, 'l:' || b.id::text))
           ORDER BY GREATEST(a.created_at, b.created_at) DESC, a.id, b.id
       ) AS group_pair_rank
FROM listing_cross_account_conflicts conflict
JOIN cards a ON a.id=conflict.left_id
JOIN cards b ON b.id=conflict.right_id
)
SELECT * FROM candidates
WHERE group_pair_rank = 1
ORDER BY GREATEST(left_collected_at, right_collected_at) DESC, left_id, right_id;
