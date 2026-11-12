SELECT id, phone, session_name, state, error, created_at, updated_at
FROM login_requests ORDER BY id DESC LIMIT 1;
