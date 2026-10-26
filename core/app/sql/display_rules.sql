-- per-source display config from the highest-priority bound rule that
-- declares one (same resolution order as the extraction claim)
SELECT b.source_id, ir.config_json->'display' AS display_cfg
FROM source_rule_bindings b
JOIN ingest_rules ir ON ir.id = b.rule_id AND ir.is_active
WHERE ir.config_json ? 'display'
ORDER BY b.source_id, b.priority DESC, ir.id;
