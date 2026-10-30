INSERT INTO profile_collection_jobs (
    rule_id, duty_date, source_id, account_id, contact_username,
    target_peer_id, target_msg_id
)
SELECT r.id, j.duty_date, j.source_id, j.account_id, j.contact_username,
       j.target_peer_id, j.target_msg_id
FROM duty_collection_jobs j
JOIN ingest_rules r ON r.rule_type = 'profile_collection' AND r.is_active
JOIN source_rule_bindings b ON b.rule_id = r.id AND b.source_id = j.source_id
WHERE j.id = $1::bigint
ON CONFLICT (rule_id, duty_date, lower(contact_username)) DO NOTHING;
