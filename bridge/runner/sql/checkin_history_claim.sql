WITH picked AS (
    SELECT h.source_id
    FROM checkin_history_scans h
    JOIN ingest_sources s ON s.id = h.source_id AND s.is_active
    JOIN bridge_accounts a ON a.id = s.assigned_account_id AND a.status = 'active'
    JOIN ingest_rules reverse_rule
      ON reverse_rule.id = h.reverse_rule_id
     AND reverse_rule.rule_type = 'checkin_reverse'
     AND reverse_rule.is_active
    WHERE (h.status IN ('pending', 'deferred')
           AND (h.next_attempt_at IS NULL OR h.next_attempt_at <= NOW())
           OR (h.status = 'processing' AND h.updated_at < NOW() - INTERVAL '10 minutes'))
      AND COALESCE(a.duty_profile_lookup_after, '-infinity'::timestamptz) <= NOW()
      AND EXISTS (
          SELECT 1 FROM source_rule_bindings b
          JOIN ingest_rules r ON r.id = b.rule_id
          WHERE b.source_id = h.source_id AND r.rule_type = 'listing' AND r.is_active
      )
    ORDER BY COALESCE(h.next_attempt_at, h.updated_at), h.source_id
    LIMIT 1 FOR UPDATE OF h SKIP LOCKED
)
UPDATE checkin_history_scans h
SET status = 'processing', error = NULL, next_attempt_at = NULL,
    started_at = COALESCE(started_at, NOW()), updated_at = NOW()
FROM picked p
JOIN ingest_sources s ON s.id = p.source_id
JOIN bridge_accounts a ON a.id = s.assigned_account_id
WHERE h.source_id = p.source_id
RETURNING h.source_id, h.reverse_rule_id, h.source_username, h.peer_id,
          h.cursor_msg_id, h.scanned_count, h.stored_count, h.candidate_count,
          a.id AS account_id, a.session_file,
          (SELECT config_json FROM source_rule_bindings b
           JOIN ingest_rules r ON r.id = b.rule_id
           WHERE b.source_id = h.source_id AND r.rule_type = 'listing' AND r.is_active
           ORDER BY b.priority DESC, r.id LIMIT 1) AS listing_config,
          (SELECT config_json FROM ingest_rules WHERE rule_type = 'profile_collection'
           AND is_active ORDER BY id LIMIT 1) AS profile_config;
