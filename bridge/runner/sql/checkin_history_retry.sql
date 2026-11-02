UPDATE checkin_history_scans
SET status = 'deferred', retry_count = retry_count + 1,
    next_attempt_at = NOW() + make_interval(secs => $2::int),
    error = $3::text, progress_note = '遇到暂时性错误，稍后从断点重试',
    updated_at = NOW()
WHERE source_id = $1::bigint;
