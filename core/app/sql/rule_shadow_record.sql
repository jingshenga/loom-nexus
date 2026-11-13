INSERT INTO rule_shadow_comparisons (
    source_id, raw_id, raw_content_hash, router_signature,
    actual_json, predicted_json, changed
)
VALUES ($1::bigint, $2::bigint, $3::varchar, $4::varchar,
        $5::jsonb, $6::jsonb, $7::boolean)
ON CONFLICT (raw_id, raw_content_hash, router_signature) DO UPDATE
SET actual_json=EXCLUDED.actual_json,
    predicted_json=EXCLUDED.predicted_json,
    changed=EXCLUDED.changed,
    observed_at=NOW();
