INSERT INTO duty_collection_jobs AS j (
    rule_id, duty_date, source_id, account_id, contact_username,
    target_peer_id, target_msg_id
)
SELECT $1::bigint, $2::date, s.id, s.assigned_account_id, lower($4::text),
       s.peer_id, $6::bigint
FROM ingest_sources s
WHERE s.id = $3::bigint
  AND s.is_active
  AND s.assigned_account_id IS NOT NULL
  AND ($5::bigint IS NULL OR s.peer_id = $5::bigint)
  AND EXISTS (
      SELECT 1 FROM source_rule_bindings b
      JOIN ingest_rules r ON r.id = b.rule_id
      WHERE b.source_id = s.id AND r.is_active AND r.rule_type = 'listing'
  )
  AND NOT EXISTS (
      SELECT 1 FROM listings l
      WHERE lower(l.contact_username) = lower($4::text)
  )
ON CONFLICT (duty_date, lower(contact_username)) DO UPDATE SET
    rule_id = EXCLUDED.rule_id,
    source_id = EXCLUDED.source_id,
    account_id = EXCLUDED.account_id,
    target_peer_id = EXCLUDED.target_peer_id,
    target_msg_id = EXCLUDED.target_msg_id,
    status = 'pending',
    raw_id = NULL,
    error = NULL,
    updated_at = NOW()
WHERE (j.status IN ('failed', 'not_found', 'cancelled'))
   OR (j.status = 'pending' AND j.target_msg_id IS NULL
       AND EXCLUDED.target_msg_id IS NOT NULL)
RETURNING id;
