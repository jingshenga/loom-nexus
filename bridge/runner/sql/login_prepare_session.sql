SELECT account_disconnect(a.id)
FROM bridge_accounts a
WHERE a.session_file = $1::text;
