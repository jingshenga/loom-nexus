SELECT status, fetched, error FROM backfill_jobs
WHERE source_id = $1::bigint ORDER BY id DESC LIMIT 1;
