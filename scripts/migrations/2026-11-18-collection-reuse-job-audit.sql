BEGIN;

ALTER TABLE profile_collection_jobs
    ADD COLUMN IF NOT EXISTS collection_plan_json JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
