SELECT j.id AS job_id, j.duty_date, j.contact_username, j.status, j.error,
       j.next_attempt_at, j.retry_count, j.created_at AS queued_at,
       r.id AS result_id, r.contact_peer_id, r.profile_url, r.channel_url,
       r.bot_url, r.description, r.media_json, r.evidence_json, r.collected_at,
       (SELECT count(*) FROM listing_media_ocr o
        WHERE o.profile_result_id = r.id AND o.status = 'pending') AS ocr_pending,
       (SELECT count(*) FROM listing_media_ocr o
        WHERE o.profile_result_id = r.id AND o.review_status = 'pending') AS ocr_review
FROM profile_collection_jobs j
LEFT JOIN profile_collection_results r ON r.job_id = j.id
WHERE j.rule_id = $1::bigint
ORDER BY j.created_at DESC, j.id DESC
LIMIT GREATEST(1, LEAST($2::int, 100));
