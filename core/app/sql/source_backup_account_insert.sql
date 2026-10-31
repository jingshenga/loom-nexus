INSERT INTO ingest_source_account_backups (source_id, account_id, priority)
VALUES ($1::bigint, $2::bigint, $3::int);
