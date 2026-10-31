SELECT j.id AS job_id, j.rule_id, j.source_id, s.title AS source_title,
       j.account_id, COALESCE(a.display_name, a.username, '账号#' || a.id::text) AS account_name,
       j.contact_username, j.status, j.stage, j.progress_current, j.progress_total,
       j.progress_note, j.started_at, j.heartbeat_at, j.updated_at,
       j.next_attempt_at, j.retry_count, j.error,
       EXTRACT(EPOCH FROM (NOW() - COALESCE(j.heartbeat_at, j.updated_at)))::int AS heartbeat_age_seconds
FROM profile_collection_jobs j
JOIN ingest_sources s ON s.id = j.source_id
JOIN bridge_accounts a ON a.id = j.account_id
WHERE j.status = 'processing'
ORDER BY j.started_at NULLS FIRST, j.id;
