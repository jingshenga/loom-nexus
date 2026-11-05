SELECT j.id AS job_id, j.duty_date, j.contact_username, j.status, j.error,
       j.next_attempt_at, j.retry_count, j.created_at AS queued_at,
       j.id AS result_id, NULL::bigint AS contact_peer_id,
       COALESCE(r.profile_url, '') AS profile_url, ''::text AS channel_url,
       COALESCE(r.bot_url, '') AS bot_url, ''::text AS description,
       '[]'::jsonb AS media_json, '{}'::jsonb AS evidence_json,
       r.collected_at, 0::bigint AS ocr_pending, 0::bigint AS ocr_review
FROM profile_collection_jobs j
LEFT JOIN bot_collection_results r ON r.job_id = j.id
WHERE j.rule_id = $1::bigint
ORDER BY j.created_at DESC, j.id DESC
LIMIT GREATEST(1, LEAST($2::int, 100));
