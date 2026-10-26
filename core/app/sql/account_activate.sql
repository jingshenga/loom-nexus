UPDATE bridge_accounts SET status = 'active' WHERE id = $1::bigint AND status = 'disconnected';
