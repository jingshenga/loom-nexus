UPDATE duty_recovery_scans
SET scanned_count = $2::int,
    scan_cursor = $3::bigint,
    candidates = $4::jsonb,
    status = $5::text,
    error = $6::text,
    updated_at = NOW()
WHERE id = $1::bigint;
