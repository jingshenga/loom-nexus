UPDATE duty_collection_jobs
SET status = $2::text,
    raw_id = $3::bigint,
    error = $4::text,
    next_attempt_at = NULL,
    updated_at = NOW()
WHERE id = $1::bigint AND status = 'processing';
