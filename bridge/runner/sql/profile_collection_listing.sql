INSERT INTO listings (
    contact_peer_id, contact_username, nickname, district, price_amount,
    price_currency, description, media_json,
    evidence_json, status, confidence, profile_collection_job_id
)
SELECT r.contact_peer_id, r.contact_username,
       NULLIF(r.evidence_json->>'nickname', ''),
       NULLIF(r.evidence_json->'extracted_fields'->>'district', ''),
       NULLIF(r.evidence_json->'extracted_fields'->>'price_amount', '')::numeric,
       COALESCE(NULLIF(r.evidence_json->'extracted_fields'->>'price_currency', ''), 'CNY'),
       r.description, r.media_json,
       r.evidence_json || jsonb_build_object('review_bucket', $2::text),
       CASE WHEN EXISTS (
           SELECT 1 FROM listings old
           WHERE old.id <> COALESCE((
               SELECT id FROM listings WHERE profile_collection_job_id = j.id
           ), 0)
             AND old.status IN ('pending', 'duplicate_pending', 'published', 'archived')
             AND (
                 (r.contact_peer_id IS NOT NULL AND old.contact_peer_id = r.contact_peer_id)
                 OR (NULLIF(r.contact_username, '') IS NOT NULL
                     AND lower(old.contact_username) = lower(r.contact_username))
                 OR (NULLIF(r.evidence_json->>'nickname', '') IS NOT NULL
                     AND lower(trim(old.nickname)) = lower(trim(r.evidence_json->>'nickname')))
             )
       ) THEN 'duplicate_pending' ELSE 'pending' END,
       0.95, j.id
FROM profile_collection_jobs j
JOIN profile_collection_results r ON r.job_id = j.id
WHERE j.id = $1::bigint
ON CONFLICT (profile_collection_job_id) WHERE profile_collection_job_id IS NOT NULL
DO UPDATE SET contact_peer_id = EXCLUDED.contact_peer_id,
              contact_username = EXCLUDED.contact_username,
              nickname = CASE
                  WHEN listings.manual_fields ? 'nickname' THEN listings.nickname
                  WHEN EXCLUDED.nickname IS NOT NULL THEN EXCLUDED.nickname
                  WHEN lower(trim(listings.nickname)) = lower(listings.contact_username)
                    OR lower(trim(listings.nickname)) = '@' || lower(listings.contact_username)
                    OR listings.nickname = 'TG ID ' || listings.contact_peer_id::text THEN NULL
                  ELSE listings.nickname
              END,
              district = CASE WHEN listings.manual_fields ? 'district'
                              THEN listings.district ELSE COALESCE(EXCLUDED.district, listings.district) END,
              price_amount = CASE WHEN listings.manual_fields ? 'price_amount'
                                  THEN listings.price_amount ELSE COALESCE(EXCLUDED.price_amount, listings.price_amount) END,
              price_currency = CASE WHEN listings.manual_fields ? 'price_currency'
                                    THEN listings.price_currency ELSE COALESCE(
                                      NULLIF(EXCLUDED.evidence_json->'extracted_fields'->>'price_currency', ''),
                                      listings.price_currency, 'CNY'
                                    ) END,
              description = EXCLUDED.description,
              media_json = EXCLUDED.media_json,
              evidence_json = EXCLUDED.evidence_json,
              updated_at = NOW()
RETURNING id;
