SELECT j.id, j.duty_date, j.contact_username, j.contact_peer_id,
       j.profile_name, j.profile_about, j.profile_checked_at,
       j.target_peer_id, j.target_msg_id, j.profile_url, j.channel_url,
       j.bot_url,
       j.status, j.error, j.updated_at, j.next_attempt_at, j.retry_count,
       COALESCE(ds.scan_target, 20) AS scan_target,
       COALESCE(ds.scanned_count, 0) AS scanned_count,
       COALESCE(ds.status, 'idle') AS recovery_scan_status,
       COALESCE(ds.candidates, '[]'::jsonb) AS candidates,
       ds.error AS scan_error,
       s.title AS source_title, s.username AS source_username
FROM duty_collection_jobs j
LEFT JOIN ingest_sources s ON s.id = j.source_id
LEFT JOIN duty_recovery_scans ds ON ds.duty_job_id = j.id
WHERE j.status IN ('not_found', 'failed', 'deferred')
ORDER BY j.updated_at DESC, j.id DESC
LIMIT 200;
