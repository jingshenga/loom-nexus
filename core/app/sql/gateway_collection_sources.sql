SELECT s.id AS source_id, s.title,
       COALESCE(a.display_name, a.username, '账号#' || a.id::text) AS primary_account,
       (SELECT jsonb_agg(jsonb_build_object(
           'name', COALESCE(ba.display_name, ba.username, '账号#' || ba.id::text),
           'status', ba.status, 'priority', b.priority
       ) ORDER BY b.priority, b.account_id)
        FROM ingest_source_account_backups b
        JOIN bridge_accounts ba ON ba.id = b.account_id
        WHERE b.source_id = s.id) AS backup_accounts,
       count(j.id) FILTER (WHERE j.status = 'pending') AS pending,
       count(j.id) FILTER (WHERE j.status = 'processing') AS processing,
       count(j.id) FILTER (WHERE j.status = 'deferred') AS deferred,
       count(j.id) FILTER (WHERE j.status = 'fetched') AS fetched,
       count(j.id) FILTER (WHERE j.status IN ('failed', 'not_found', 'stale_source', 'source_unavailable')) AS needs_attention
FROM ingest_sources s
JOIN source_rule_bindings b ON b.source_id = s.id
JOIN ingest_rules r ON r.id = b.rule_id AND r.rule_type = 'profile_collection' AND r.is_active
LEFT JOIN bridge_accounts a ON a.id = s.assigned_account_id
LEFT JOIN profile_collection_jobs j ON j.source_id = s.id
GROUP BY s.id, s.title, a.id, a.display_name, a.username
ORDER BY s.id;
