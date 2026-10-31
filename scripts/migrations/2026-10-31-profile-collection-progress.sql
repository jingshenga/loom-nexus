ALTER TABLE profile_collection_jobs
    ADD COLUMN IF NOT EXISTS stage VARCHAR(32) NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS progress_current INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS progress_total INT,
    ADD COLUMN IF NOT EXISTS progress_note VARCHAR(240),
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;

UPDATE profile_collection_jobs
SET started_at = COALESCE(started_at, updated_at),
    heartbeat_at = COALESCE(heartbeat_at, updated_at),
    stage = CASE WHEN status = 'processing' THEN 'in_progress' ELSE stage END
WHERE status = 'processing';
