SELECT DISTINCT s.id AS source_id, s.title, s.username
FROM ingest_sources s
JOIN source_rule_bindings b ON b.source_id = s.id
JOIN ingest_rules r ON r.id = b.rule_id
WHERE s.is_active AND r.is_active AND r.rule_type = 'listing'
ORDER BY s.id;
