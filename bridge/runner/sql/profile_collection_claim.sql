WITH picked AS (
    SELECT j.id
    FROM profile_collection_jobs j
    LEFT JOIN bridge_accounts a ON a.id = j.account_id
    WHERE (j.status = 'pending'
        OR (j.status = 'deferred' AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= NOW()))
        OR (j.status = 'processing' AND j.updated_at < NOW() - INTERVAL '10 minutes'))
      AND COALESCE(a.duty_profile_lookup_after, '-infinity'::timestamptz) <= NOW()
    ORDER BY COALESCE(j.next_attempt_at, j.updated_at), j.id
    LIMIT 1 FOR UPDATE OF j SKIP LOCKED
)
UPDATE profile_collection_jobs j
SET status = 'processing', error = NULL, next_attempt_at = NULL, updated_at = NOW()
FROM picked p
WHERE j.id = p.id
RETURNING j.id AS job_id, j.rule_id, j.duty_date, j.source_id, j.account_id,
          j.contact_username, j.target_peer_id, j.target_msg_id,
          ''::text AS channel_url, ''::text AS bot_url,
          (SELECT s.username FROM ingest_sources s WHERE s.id = j.source_id) AS source_username,
          (SELECT a.session_file FROM bridge_accounts a
           WHERE a.id = j.account_id AND a.status = 'active') AS session_file,
          (SELECT config_json FROM ingest_rules r WHERE r.id = j.rule_id) AS config_json;
