SELECT 1
FROM ingest_sources s
WHERE s.id = $1::bigint
  AND s.is_active
  AND s.assigned_account_id IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM source_rule_bindings b
      JOIN ingest_rules r ON r.id = b.rule_id
      WHERE b.source_id = s.id AND r.is_active AND r.rule_type = 'listing'
  );
