UPDATE bridge_accounts
SET duty_profile_lookup_after = GREATEST(
    COALESCE(duty_profile_lookup_after, NOW()),
    NOW() + $2::int * INTERVAL '1 second'
)
WHERE id = $1::bigint;
