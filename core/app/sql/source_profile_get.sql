SELECT extraction_profile, profile_learned_at
FROM ingest_sources
WHERE id = $1::bigint;
