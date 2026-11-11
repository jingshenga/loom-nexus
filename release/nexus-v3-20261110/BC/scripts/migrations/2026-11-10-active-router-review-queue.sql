-- Active Router sends ambiguous or incomplete candidates to an auditable queue.
-- Additive only; no existing messages or listings are changed.
BEGIN;

CREATE TABLE IF NOT EXISTS rule_active_review_queue (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    raw_id BIGINT NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
    content_hash VARCHAR(64) NOT NULL,
    reason VARCHAR(64) NOT NULL,
    candidate_rule_ids BIGINT[] NOT NULL DEFAULT '{}',
    diagnostics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_id,raw_id,content_hash)
);

CREATE INDEX IF NOT EXISTS idx_rule_active_review_open
    ON rule_active_review_queue(created_at DESC) WHERE status='open';

ALTER TABLE rule_active_review_queue
    ADD COLUMN IF NOT EXISTS candidate_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reviewed_by BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_action VARCHAR(32) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS rule_active_review_events (
    id BIGSERIAL PRIMARY KEY,
    queue_id BIGINT NOT NULL REFERENCES rule_active_review_queue(id) ON DELETE CASCADE,
    actor_user_id BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    action VARCHAR(32) NOT NULL CHECK (action IN ('dismiss','no_action','create_pending_listing')),
    listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
    decision_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rule_active_review_events_queue
    ON rule_active_review_events(queue_id,created_at DESC);
ALTER TABLE rule_active_review_events
    ADD COLUMN IF NOT EXISTS decision_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE rule_parse_runs DROP CONSTRAINT IF EXISTS rule_parse_runs_run_reason_check;
ALTER TABLE rule_parse_runs ADD CONSTRAINT rule_parse_runs_run_reason_check
    CHECK (run_reason IN ('legacy','shadow','dry_run','reparse','active'));

COMMIT;
