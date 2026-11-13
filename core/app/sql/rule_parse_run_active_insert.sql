INSERT INTO rule_parse_runs (
    raw_revision_id,purpose,rule_id,rule_version_id,parser_key,
    run_reason,idempotency_key,trace_json,result_json
)
VALUES ($1::bigint,$2::varchar,$3::bigint,$4::bigint,$5::varchar,
        'active',$6::varchar,$7::jsonb,$8::jsonb)
ON CONFLICT (idempotency_key) DO NOTHING;
