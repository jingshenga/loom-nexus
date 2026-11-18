BEGIN;

ALTER TABLE profile_collection_jobs
    ADD COLUMN IF NOT EXISTS collection_plan_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS collection_reuse_mode_events (
    id BIGSERIAL PRIMARY KEY,
    previous_enabled BOOLEAN NOT NULL,
    enabled BOOLEAN NOT NULL,
    actor_user_id BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
