WITH deferred AS (
    UPDATE profile_collection_jobs
    SET status = 'deferred', next_attempt_at = NOW() + make_interval(secs => $2::int),
        retry_count = retry_count + 1, error = 'Telegram FloodWait',
        stage = 'cooldown', progress_note = 'Telegram FloodWait，等待冷却后重试',
        heartbeat_at = NOW(), updated_at = NOW()
    WHERE id = $1::bigint
    RETURNING account_id
)
UPDATE bridge_accounts
SET duty_profile_lookup_after = GREATEST(
    COALESCE(duty_profile_lookup_after, '-infinity'::timestamptz),
    NOW() + make_interval(secs => $2::int)
)
WHERE id = (SELECT account_id FROM deferred);
