INSERT INTO listings (
    contact_peer_id, contact_username, nickname, district, bot_url, description, media_json,
    evidence_json, status, confidence, profile_collection_job_id
)
SELECT r.contact_peer_id, r.contact_username,
       NULLIF(r.evidence_json->>'nickname', ''),
       NULLIF(r.evidence_json->>'district', ''),
       COALESCE(
           NULLIF(BTRIM(r.bot_url), ''),
           (
               SELECT NULLIF(BTRIM(bot.bot_url), '')
               FROM bot_collection_results bot
               WHERE lower(bot.contact_username) = lower(r.contact_username)
                 AND NULLIF(BTRIM(bot.bot_url), '') IS NOT NULL
               ORDER BY bot.collected_at DESC, bot.job_id DESC
               LIMIT 1
           ),
           ''
       ),
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
              nickname = CASE WHEN listings.manual_fields ? 'nickname' THEN listings.nickname
                              ELSE COALESCE(EXCLUDED.nickname, listings.nickname) END,
              district = CASE WHEN listings.manual_fields ? 'district' THEN listings.district
                              ELSE COALESCE(EXCLUDED.district, listings.district) END,
              bot_url = CASE WHEN listings.manual_fields ? 'bot_url' THEN listings.bot_url ELSE EXCLUDED.bot_url END,
              description = EXCLUDED.description,
              media_json = EXCLUDED.media_json,
              evidence_json = EXCLUDED.evidence_json,
              updated_at = NOW()
RETURNING id;
