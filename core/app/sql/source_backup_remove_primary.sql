DELETE FROM ingest_source_account_backups
WHERE source_id = $1::bigint AND account_id = $2::bigint;
