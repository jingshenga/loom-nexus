DELETE FROM dynamic_slang_terms
WHERE id = $1::bigint
RETURNING id;
