INSERT INTO dynamic_slang_terms (category, source_term, target_action, factor, enabled)
VALUES ($1::varchar, $2::varchar, $3::varchar, $4::int, $5::boolean)
ON CONFLICT (category, source_term) DO UPDATE SET
    target_action = EXCLUDED.target_action,
    factor = EXCLUDED.factor,
    enabled = EXCLUDED.enabled,
    updated_at = NOW()
RETURNING id, category, source_term, target_action, factor, enabled;
