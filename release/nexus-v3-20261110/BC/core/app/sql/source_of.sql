SELECT id, peer_id, assigned_account_id FROM ingest_sources WHERE id = $1::bigint;
