SELECT s.id AS source_id, s.peer_id, s.title,
       s.assigned_account_id AS account_id, 0 AS priority,
       (
           NOT EXISTS (
               SELECT 1 FROM source_rule_bindings b
               JOIN ingest_rules r ON r.id = b.rule_id
               WHERE b.source_id = s.id AND r.is_active
                 AND r.rule_type = 'on_duty'
           )
           OR EXISTS (
               SELECT 1 FROM source_rule_bindings b
               JOIN ingest_rules r ON r.id = b.rule_id
               WHERE b.source_id = s.id AND r.is_active
                 AND r.rule_type = 'listing'
           )
       ) AS listen_source
FROM ingest_sources s
WHERE s.is_active AND s.assigned_account_id IS NOT NULL
UNION ALL
SELECT s.id AS source_id, s.peer_id, s.title,
       b.account_id, b.priority,
       (
           NOT EXISTS (
               SELECT 1 FROM source_rule_bindings rb
               JOIN ingest_rules r ON r.id = rb.rule_id
               WHERE rb.source_id = s.id AND r.is_active
                 AND r.rule_type = 'on_duty'
           )
           OR EXISTS (
               SELECT 1 FROM source_rule_bindings rb
               JOIN ingest_rules r ON r.id = rb.rule_id
               WHERE rb.source_id = s.id AND r.is_active
                 AND r.rule_type = 'listing'
           )
       ) AS listen_source
FROM ingest_source_account_backups b
JOIN ingest_sources s ON s.id = b.source_id
WHERE s.is_active
ORDER BY source_id, priority, account_id;
