SELECT state, error FROM bridge_disconnect_requests WHERE id = $1::bigint;
