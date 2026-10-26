SELECT id, phone, session_name, state FROM login_requests WHERE id = $1::bigint;
