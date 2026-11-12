INSERT INTO duty_recovery_scans (duty_job_id, scan_target, status, error, updated_at)
VALUES ($1::bigint, $2::int, 'pending', NULL, NOW())
ON CONFLICT (duty_job_id) DO UPDATE
SET scan_target = EXCLUDED.scan_target, status = 'pending', error = NULL, updated_at = NOW()
WHERE duty_recovery_scans.status <> 'processing'
RETURNING id;
