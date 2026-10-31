UPDATE bridge_accounts
SET status = 'active', last_active_at = NOW()
WHERE id = $1::bigint AND status = 'disconnected';
