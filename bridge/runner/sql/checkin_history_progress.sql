UPDATE checkin_history_scans
SET cursor_msg_id = $2::bigint,
    scanned_count = scanned_count + $3::int,
    stored_count = stored_count + $4::int,
    candidate_count = candidate_count + $5::int,
    last_scanned_msg_id = $6::bigint,
    last_scanned_at = $7::timestamptz,
    status = CASE WHEN $8::boolean THEN 'pending' ELSE 'completed' END,
    next_attempt_at = CASE WHEN $8::boolean
        THEN NOW() + make_interval(secs => $9::int) ELSE NULL END,
    completed_at = CASE WHEN $8::boolean THEN NULL ELSE NOW() END,
    progress_note = CASE WHEN $8::boolean THEN '历史消息已扫描，等待下一批' ELSE '已扫描到频道最早消息' END,
    error = NULL, updated_at = NOW()
WHERE source_id = $1::bigint;
