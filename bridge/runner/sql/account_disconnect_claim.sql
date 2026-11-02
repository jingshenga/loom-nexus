WITH candidate AS (
    SELECT r.id, r.account_id, r.action, a.session_file
    FROM bridge_disconnect_requests r
    JOIN bridge_accounts a ON a.id = r.account_id
    WHERE r.state = 'pending'
    ORDER BY r.id
    FOR UPDATE OF r SKIP LOCKED
    LIMIT 1
)
UPDATE bridge_disconnect_requests r
SET state = 'processing'
FROM candidate c
WHERE r.id = c.id
RETURNING r.id AS request_id, c.account_id, c.action, c.session_file;
