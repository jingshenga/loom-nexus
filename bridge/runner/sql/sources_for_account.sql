SELECT id, peer_id, title
FROM ingest_sources
WHERE is_active AND assigned_account_id = $1::bigint
ORDER BY id;
