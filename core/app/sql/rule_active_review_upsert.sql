INSERT INTO rule_active_review_queue(
    source_id, raw_id, content_hash, reason, candidate_rule_ids, diagnostics_json, candidate_json
)
VALUES($1,$2,$3,$4,$5::bigint[],$6::jsonb,$7::jsonb)
ON CONFLICT(source_id,raw_id,content_hash) DO UPDATE
SET reason=EXCLUDED.reason,
    candidate_rule_ids=EXCLUDED.candidate_rule_ids,
    diagnostics_json=EXCLUDED.diagnostics_json,
    candidate_json=EXCLUDED.candidate_json,
    updated_at=NOW()
WHERE rule_active_review_queue.status='open';
