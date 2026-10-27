SELECT s.id, s.peer_id, s.title
FROM ingest_sources s
WHERE is_active AND assigned_account_id = $1::bigint
  AND (
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
  )
ORDER BY id;
