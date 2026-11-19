UPDATE dynamic_slang_terms
SET enabled = $2::boolean, updated_at = NOW()
WHERE id = $1::bigint
RETURNING id, enabled;
