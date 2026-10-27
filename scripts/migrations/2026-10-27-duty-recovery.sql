ALTER TABLE duty_collection_jobs
    ADD COLUMN IF NOT EXISTS contact_peer_id BIGINT,
    ADD COLUMN IF NOT EXISTS profile_url TEXT,
    ADD COLUMN IF NOT EXISTS channel_url TEXT,
    ADD COLUMN IF NOT EXISTS bot_url TEXT;

CREATE INDEX IF NOT EXISTS idx_duty_collection_recovery
    ON duty_collection_jobs(status, updated_at DESC)
    WHERE status IN ('not_found', 'failed');

CREATE TABLE IF NOT EXISTS duty_recovery_scans (
    id BIGSERIAL PRIMARY KEY,
    duty_job_id BIGINT NOT NULL UNIQUE REFERENCES duty_collection_jobs(id) ON DELETE CASCADE,
    scan_target INT NOT NULL DEFAULT 20 CHECK (scan_target IN (20, 50, 100)),
    scanned_count INT NOT NULL DEFAULT 0,
    scan_cursor BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'idle',
    candidates JSONB NOT NULL DEFAULT '[]',
    error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
