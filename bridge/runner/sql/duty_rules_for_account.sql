SELECT s.id AS source_id, s.peer_id, s.title,
       r.id AS rule_id, r.rule_name, r.config_json
FROM ingest_sources s
JOIN source_rule_bindings b ON b.source_id = s.id
JOIN ingest_rules r ON r.id = b.rule_id
WHERE s.is_active
  AND s.assigned_account_id = $1::bigint
  AND r.is_active
  AND r.rule_type = 'on_duty'
ORDER BY s.id, b.priority DESC, r.id;
