INSERT INTO ingest_rules (rule_name, rule_type, version, config_json, is_active)
SELECT $2::varchar, rule_type, 1, config_json, FALSE
FROM ingest_rules
WHERE id = $1::bigint AND rule_name <> 'default-rule'
RETURNING id AS rule_id, version;
