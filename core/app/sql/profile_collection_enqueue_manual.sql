WITH candidate AS (
    SELECT r.id AS rule_id, s.id AS source_id, s.assigned_account_id AS account_id
    FROM ingest_rules r
    JOIN source_rule_bindings b ON b.rule_id = r.id
    JOIN ingest_sources s ON s.id = b.source_id
    JOIN bridge_accounts a ON a.id = s.assigned_account_id
    WHERE r.id = $1::bigint AND r.rule_type IN ('profile_collection', 'bot_collection') AND r.is_active
      AND ($3::bigint IS NULL OR s.id = $3::bigint)
      AND s.is_active AND a.status = 'active'
    ORDER BY COALESCE(a.duty_profile_lookup_after, '-infinity'::timestamptz),
             b.priority DESC, s.id
    LIMIT 1
), precheck AS MATERIALIZED (
    SELECT candidate.*, nexus_bot_collection_precheck(rule_id, source_id, $2::text) AS skip_reason
    FROM candidate
), inserted AS (
    INSERT INTO profile_collection_jobs (
        rule_id, duty_date, source_id, account_id, contact_username, target_peer_id
    )
    SELECT rule_id, (NOW() AT TIME ZONE 'Asia/Shanghai')::date,
           source_id, account_id, $2::text, NULL
    FROM precheck WHERE skip_reason IS NULL
    ON CONFLICT (rule_id, duty_date, lower(contact_username))
        WHERE target_msg_id IS NULL DO UPDATE SET
        status = CASE
            WHEN profile_collection_jobs.status IN ('pending', 'processing', 'deferred')
                THEN profile_collection_jobs.status
            ELSE 'pending'
        END,
        next_attempt_at = CASE WHEN profile_collection_jobs.status = 'deferred'
            THEN profile_collection_jobs.next_attempt_at ELSE NULL END,
        error = CASE WHEN profile_collection_jobs.status = 'deferred'
            THEN profile_collection_jobs.error ELSE NULL END,
        updated_at = NOW()
    RETURNING id, status
)
SELECT id AS job_id, status, NULL::text AS skip_reason FROM inserted
UNION ALL SELECT NULL::bigint, 'skipped', skip_reason FROM precheck WHERE skip_reason IS NOT NULL;
