BEGIN;

CREATE TABLE IF NOT EXISTS vision4_model_versions (
    id BIGSERIAL PRIMARY KEY,
    version_key VARCHAR(64) NOT NULL UNIQUE,
    model_path TEXT NOT NULL UNIQUE,
    model_sha256 VARCHAR(64) NOT NULL CHECK (model_sha256 ~ '^[0-9a-f]{64}$'),
    status VARCHAR(16) NOT NULL CHECK (status IN ('ready','active','rollback')),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vision4_model_single_active
    ON vision4_model_versions(status) WHERE status='active';

CREATE TABLE IF NOT EXISTS vision4_model_events (
    id BIGSERIAL PRIMARY KEY,
    model_version_id BIGINT REFERENCES vision4_model_versions(id) ON DELETE SET NULL,
    action VARCHAR(24) NOT NULL CHECK (action IN ('installed','activated','rolled_back')),
    actor_id BIGINT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vision4_model_events_created
    ON vision4_model_events(created_at DESC, id DESC);

COMMIT;
