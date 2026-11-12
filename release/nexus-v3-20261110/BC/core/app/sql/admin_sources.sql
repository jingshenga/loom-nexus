SELECT s.id AS source_id, s.peer_id, s.title, s.username, s.is_active,
       s.assigned_account_id AS account_id, a.display_name AS account_name,
       s.last_msg_id,
       (SELECT count(*) FROM raw_messages rm WHERE rm.source_id = s.id) AS raw_count,
       COALESCE((
           SELECT jsonb_agg(jsonb_build_object(
               'id', b.account_id,
               'name', ba.display_name,
               'priority', b.priority
           ) ORDER BY b.priority, b.account_id)
           FROM ingest_source_account_backups b
           JOIN bridge_accounts ba ON ba.id = b.account_id
           WHERE b.source_id = s.id
       ), '[]'::jsonb) AS backup_accounts
FROM ingest_sources s
LEFT JOIN bridge_accounts a ON a.id = s.assigned_account_id
ORDER BY s.id;
