SELECT EXISTS (
    SELECT 1
    FROM profile_collection_jobs j
    JOIN ingest_rules r ON r.id = j.rule_id AND r.is_active
    WHERE (
        j.status = 'pending'
        OR (j.status = 'deferred' AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= NOW()))
        OR (j.status = 'processing' AND j.updated_at < NOW() - INTERVAL '10 minutes')
    )
      AND EXISTS (
          SELECT 1
          FROM (
              SELECT s.assigned_account_id AS account_id
              FROM ingest_sources s
              WHERE s.id = j.source_id AND s.is_active
              UNION ALL
              SELECT b.account_id
              FROM ingest_source_account_backups b
              WHERE b.source_id = j.source_id
          ) candidates
          JOIN bridge_accounts a ON a.id = candidates.account_id
          WHERE a.status = 'active'
            AND COALESCE(a.duty_profile_lookup_after, '-infinity'::timestamptz) <= NOW()
      )
);
