WITH claimed AS (
    SELECT j.id, j.listing_id, j.raw_id
    FROM listing_recollect_jobs j
    WHERE j.status = 'pending'
       OR (j.status = 'processing' AND j.updated_at < NOW() - INTERVAL '10 minutes')
    ORDER BY j.id
    LIMIT 1
    FOR UPDATE OF j SKIP LOCKED
)
UPDATE listing_recollect_jobs j
SET status = 'processing', error = NULL, updated_at = NOW()
FROM claimed c
LEFT JOIN listings l ON l.id = c.listing_id
LEFT JOIN raw_messages rm ON rm.id = c.raw_id
LEFT JOIN ingest_sources s ON s.id = rm.source_id
LEFT JOIN bridge_accounts a ON a.id = s.assigned_account_id AND a.status = 'active'
WHERE j.id = c.id
RETURNING j.id AS job_id, j.listing_id, j.raw_id,
          rm.platform_msg_id, s.peer_id, a.session_file, l.id AS current_listing_id;
