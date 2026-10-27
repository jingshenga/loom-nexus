SELECT id AS job_id, listing_id, status, error, updated_at
FROM listing_recollect_jobs
WHERE id = ANY($1::bigint[])
ORDER BY id;
