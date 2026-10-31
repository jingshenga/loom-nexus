UPDATE bridge_accounts
SET status = 'needs_auth'
WHERE id = $1::bigint AND status = 'active';
