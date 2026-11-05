UPDATE profile_collection_jobs
SET stage = $2::text,
    progress_current = COALESCE($3::int, progress_current),
    progress_total = $4::int,
    progress_note = $5::text,
    heartbeat_at = NOW(),
    updated_at = NOW()
WHERE id = $1::bigint AND status = 'processing';
