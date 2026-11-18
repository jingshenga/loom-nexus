UPDATE profile_collection_jobs
SET collection_plan_json = $2::jsonb
WHERE id = $1::bigint;
