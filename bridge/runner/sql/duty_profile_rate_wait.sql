SELECT GREATEST(0, CEIL(EXTRACT(EPOCH FROM (duty_profile_lookup_after - NOW())))::int)
FROM bridge_accounts
WHERE id = $1::bigint;
