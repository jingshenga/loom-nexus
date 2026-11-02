WITH deferred AS (
    UPDATE checkin_history_scans
    SET status = 'deferred', next_attempt_at = NOW() + make_interval(secs => $2::int),
        error = 'Telegram FloodWait', progress_note = 'Telegram 冷却中，等待后从断点续扫',
        updated_at = NOW()
    WHERE source_id = $1::bigint
    RETURNING source_id
)
UPDATE bridge_accounts a
SET duty_profile_lookup_after = GREATEST(
    COALESCE(a.duty_profile_lookup_after, '-infinity'::timestamptz),
    NOW() + make_interval(secs => $2::int)
)
FROM ingest_sources s
WHERE s.id = (SELECT source_id FROM deferred)
  AND a.id = s.assigned_account_id;
