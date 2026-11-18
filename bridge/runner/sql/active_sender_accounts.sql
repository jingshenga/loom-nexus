SELECT id, account_uid, display_name, username, session_file
FROM bridge_accounts
WHERE status = 'active'
  AND (flood_wait_until IS NULL OR flood_wait_until <= NOW())
ORDER BY id;
