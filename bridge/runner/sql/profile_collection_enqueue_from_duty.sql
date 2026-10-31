INSERT INTO profile_collection_jobs (
    rule_id, duty_date, source_id, account_id, contact_username,
    target_peer_id, target_msg_id
)
SELECT r.id, $1::date, s.id, s.assigned_account_id, lower($3::text),
       $4::bigint, $5::bigint
FROM ingest_sources s
JOIN source_rule_bindings b ON b.source_id = s.id
JOIN ingest_rules r ON r.id = b.rule_id
WHERE s.id = $2::bigint
  AND s.is_active
  AND s.assigned_account_id IS NOT NULL
  AND r.rule_type = 'profile_collection'
  AND r.is_active
ON CONFLICT (rule_id, duty_date, lower(contact_username)) DO NOTHING
RETURNING id;
