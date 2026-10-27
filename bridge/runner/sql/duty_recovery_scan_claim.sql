WITH picked AS (
    SELECT s.id
    FROM duty_recovery_scans s
    WHERE s.status = 'pending'
       OR (s.status = 'processing' AND s.updated_at < NOW() - INTERVAL '10 minutes')
    ORDER BY s.updated_at, s.id
    LIMIT 1 FOR UPDATE SKIP LOCKED
)
UPDATE duty_recovery_scans s
SET status = 'processing', error = NULL, updated_at = NOW()
FROM picked p, duty_collection_jobs j
WHERE s.id = p.id AND j.id = s.duty_job_id
RETURNING s.id AS scan_id, s.duty_job_id, s.scan_target, s.scanned_count,
          s.scan_cursor, s.candidates, j.contact_username, j.channel_url,
          (SELECT a.session_file FROM bridge_accounts a
           WHERE a.id = j.account_id AND a.status = 'active') AS session_file;
