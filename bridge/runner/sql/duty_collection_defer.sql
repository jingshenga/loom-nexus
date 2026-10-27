WITH deferred AS (
    UPDATE duty_collection_jobs
    SET status = 'deferred',
        error = 'Telegram temporarily limited profile lookup; automatic retry scheduled',
        retry_count = retry_count + 1,
        next_attempt_at = NOW() + ($3::int + 120) * INTERVAL '1 second',
        updated_at = NOW()
    WHERE id = $1::bigint AND account_id = $2::bigint
    RETURNING account_id, next_attempt_at
)
UPDATE bridge_accounts a
SET duty_profile_lookup_after = GREATEST(
    COALESCE(a.duty_profile_lookup_after, NOW()),
    d.next_attempt_at
)
FROM deferred d
WHERE a.id = d.account_id;
