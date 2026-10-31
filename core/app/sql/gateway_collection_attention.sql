SELECT j.id AS job_id, j.rule_id, j.source_id, s.title AS source_title,
       j.account_id, COALESCE(a.display_name, a.username, '账号#' || a.id::text) AS account_name,
       j.contact_username, j.status, j.progress_note, j.retry_count,
       j.error, j.next_attempt_at, j.updated_at
FROM profile_collection_jobs j
JOIN ingest_sources s ON s.id = j.source_id
JOIN bridge_accounts a ON a.id = j.account_id
WHERE j.status IN ('deferred', 'failed', 'not_found', 'stale_source', 'source_unavailable')
ORDER BY CASE j.status WHEN 'failed' THEN 0 WHEN 'deferred' THEN 1 ELSE 2 END,
         j.updated_at DESC, j.id DESC
LIMIT 50;
