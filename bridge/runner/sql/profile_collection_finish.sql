UPDATE profile_collection_jobs
SET status = $2::text, error = $3::text,
    stage = CASE WHEN $2::text = 'fetched' THEN 'completed'
                 WHEN $2::text = 'skipped' THEN 'checked'
                 WHEN $2::text = 'needs_review' THEN 'needs_review'
                 WHEN $2::text IN ('not_found', 'stale_source', 'source_unavailable') THEN 'checked'
                 ELSE 'failed' END,
    progress_note = COALESCE(NULLIF($3::text, ''), $2::text),
    heartbeat_at = NOW(), updated_at = NOW()
WHERE id = $1::bigint;
