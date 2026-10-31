CREATE TABLE IF NOT EXISTS ingest_source_account_backups (
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES bridge_accounts(id) ON DELETE CASCADE,
    priority INT NOT NULL DEFAULT 1 CHECK (priority > 0),
    PRIMARY KEY (source_id, account_id),
    UNIQUE (source_id, priority)
);
