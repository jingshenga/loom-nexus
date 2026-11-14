BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS active_router_guard_state (
    source_id BIGINT PRIMARY KEY REFERENCES ingest_sources(id) ON DELETE CASCADE,
    heartbeat_at TIMESTAMPTZ,
    health_status TEXT NOT NULL DEFAULT 'starting'
        CHECK (health_status IN ('starting','healthy','degraded')),
    active_started_at TIMESTAMPTZ,
    active_baseline_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_error TEXT,
    last_rollback_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ocr_classifier_engine_events (
    id BIGSERIAL PRIMARY KEY,
    previous_engine TEXT,
    new_engine TEXT NOT NULL CHECK (new_engine IN ('vision4','legacy')),
    actor_user_id BIGINT REFERENCES site_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_active_router_guard_heartbeat
    ON active_router_guard_state(heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_ocr_classifier_engine_events_created
    ON ocr_classifier_engine_events(created_at DESC, id DESC);

COMMIT;
