UPDATE bridge_disconnect_requests
SET state = CASE WHEN $2::text IS NULL THEN 'done' ELSE 'failed' END,
    error = $2::text,
    finished_at = NOW()
WHERE id = $1::bigint AND state = 'processing';
