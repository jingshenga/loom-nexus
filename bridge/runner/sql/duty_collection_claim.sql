WITH picked AS (
    SELECT id
    FROM duty_collection_jobs
    WHERE status = 'pending'
       OR (status = 'processing' AND updated_at < NOW() - INTERVAL '10 minutes')
    ORDER BY id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
UPDATE duty_collection_jobs j
SET status = 'processing', error = NULL, updated_at = NOW()
FROM picked p
WHERE j.id = p.id
RETURNING j.id AS job_id, j.rule_id, j.duty_date, j.source_id,
          j.contact_username, j.target_peer_id, j.target_msg_id,
          (SELECT s.peer_id FROM ingest_sources s WHERE s.id = j.source_id) AS source_peer_id,
          (SELECT a.session_file FROM bridge_accounts a
           WHERE a.id = j.account_id AND a.status = 'active') AS session_file;
