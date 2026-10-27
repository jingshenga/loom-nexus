UPDATE listing_recollect_jobs
SET status = $2::text, error = $3::text, updated_at = NOW()
WHERE id = $1::bigint
  AND status = 'processing';
