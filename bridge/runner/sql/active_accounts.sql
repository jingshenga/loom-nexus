SELECT id, account_uid, display_name, username, session_file
FROM bridge_accounts
WHERE status = 'active'
ORDER BY id;
