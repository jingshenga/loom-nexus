UPDATE ingest_sources
SET extraction_profile = $2::jsonb, profile_learned_at = NOW()
WHERE id = $1::bigint
RETURNING id;
