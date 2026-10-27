SELECT s.id AS source_id, s.assigned_account_id AS account_id, s.peer_id, s.is_active
FROM ingest_sources s
WHERE s.is_active
  AND s.assigned_account_id IS NOT NULL
  AND (
      NOT EXISTS (
          SELECT 1 FROM source_rule_bindings rb
          JOIN ingest_rules ir ON ir.id = rb.rule_id
          WHERE rb.source_id = s.id AND ir.is_active
            AND ir.rule_type = 'on_duty'
      )
      OR EXISTS (
          SELECT 1 FROM source_rule_bindings rb
          JOIN ingest_rules ir ON ir.id = rb.rule_id
          WHERE rb.source_id = s.id AND ir.is_active
            AND ir.rule_type = 'listing'
      )
  );
