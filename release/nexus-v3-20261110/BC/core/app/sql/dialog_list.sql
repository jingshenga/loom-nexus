SELECT dc.account_id, dc.peer_id, dc.title, dc.username, dc.kind,
       dc.refreshed_at, a.display_name AS account_name
FROM dialog_cache dc
LEFT JOIN bridge_accounts a ON a.id = dc.account_id
ORDER BY dc.title;
