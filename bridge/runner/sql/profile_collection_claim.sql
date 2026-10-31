WITH picked AS (
    -- Route each independent V2 job to the least-recently-used active
    -- primary/backup account that is outside its Telegram cooldown.
    SELECT j.id, route.account_id
    FROM profile_collection_jobs j
    CROSS JOIN LATERAL (
        SELECT candidate.account_id
        FROM (
            SELECT s.assigned_account_id AS account_id, 0 AS priority
            FROM ingest_sources s
            WHERE s.id = j.source_id AND s.is_active
            UNION ALL
            SELECT b.account_id, b.priority
            FROM ingest_source_account_backups b
            WHERE b.source_id = j.source_id
        ) candidate
        JOIN bridge_accounts a ON a.id = candidate.account_id
        WHERE a.status = 'active'
          AND COALESCE(a.duty_profile_lookup_after, '-infinity'::timestamptz) <= NOW()
        ORDER BY (
            SELECT max(previous.updated_at)
            FROM profile_collection_jobs previous
            WHERE previous.source_id = j.source_id
              AND previous.id <> j.id
              AND previous.account_id = candidate.account_id
        ) NULLS FIRST, candidate.priority, candidate.account_id
        LIMIT 1
    ) route
    WHERE (j.status = 'pending'
        OR (j.status = 'deferred' AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= NOW()))
        OR (j.status = 'processing' AND j.updated_at < NOW() - INTERVAL '10 minutes'))
    ORDER BY COALESCE(j.next_attempt_at, j.updated_at), j.id
    LIMIT 1 FOR UPDATE OF j SKIP LOCKED
)
UPDATE profile_collection_jobs j
SET account_id = p.account_id, status = 'processing', error = NULL,
    next_attempt_at = NULL, updated_at = NOW()
FROM picked p
WHERE j.id = p.id
RETURNING j.id AS job_id, j.rule_id, j.duty_date, j.source_id, j.account_id,
          j.contact_username, j.target_peer_id, j.target_msg_id,
          ''::text AS channel_url, ''::text AS bot_url,
          (SELECT s.username FROM ingest_sources s WHERE s.id = j.source_id) AS source_username,
          (SELECT a.session_file FROM bridge_accounts a
           WHERE a.id = j.account_id AND a.status = 'active') AS session_file,
          (SELECT config_json FROM ingest_rules r WHERE r.id = j.rule_id) AS config_json;
