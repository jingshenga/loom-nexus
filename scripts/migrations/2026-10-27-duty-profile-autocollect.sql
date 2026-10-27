ALTER TABLE duty_collection_jobs
    ADD COLUMN IF NOT EXISTS profile_name TEXT,
    ADD COLUMN IF NOT EXISTS profile_about TEXT,
    ADD COLUMN IF NOT EXISTS personal_channel_id BIGINT,
    ADD COLUMN IF NOT EXISTS pinned_msg_id BIGINT,
    ADD COLUMN IF NOT EXISTS pinned_text TEXT,
    ADD COLUMN IF NOT EXISTS profile_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS retry_count INT NOT NULL DEFAULT 0;

ALTER TABLE bridge_accounts ADD COLUMN IF NOT EXISTS duty_profile_lookup_after TIMESTAMPTZ;

ALTER TABLE listings ADD COLUMN IF NOT EXISTS duty_collection_job_id BIGINT
    REFERENCES duty_collection_jobs(id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_duty_collection_job
    ON listings(duty_collection_job_id)
    WHERE duty_collection_job_id IS NOT NULL;

DROP INDEX IF EXISTS idx_duty_collection_recovery;
CREATE INDEX idx_duty_collection_recovery
    ON duty_collection_jobs(status, updated_at DESC)
    WHERE status IN ('not_found', 'failed', 'deferred');
