-- Add review evidence, router-mode audit, and bounded Shadow diagnostics.
-- This migration is additive; no source mode or listing state is changed.
BEGIN;

ALTER TABLE lifecycle_review_events
    ADD COLUMN IF NOT EXISTS identity_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE OR REPLACE FUNCTION extract_lifecycle_review_enqueue(
    p_raw_id BIGINT, p_rule_version_id BIGINT, p_sender_peer_id BIGINT,
    p_term VARCHAR(64), p_candidate_username VARCHAR(128), p_reason VARCHAR(64),
    p_identity_evidence JSONB
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO lifecycle_review_events(
        raw_id,rule_version_id,sender_peer_id,lifecycle_term,candidate_username,
        review_reason,identity_evidence
    ) VALUES (
        p_raw_id,
        COALESCE(p_rule_version_id,(SELECT rule_version_id FROM raw_messages WHERE id=p_raw_id)),
        p_sender_peer_id,p_term,
        COALESCE(NULLIF(lower(btrim(p_candidate_username)),''),''),p_reason,
        COALESCE(p_identity_evidence,'{}'::jsonb)
    )
    ON CONFLICT(raw_id,lifecycle_term,candidate_username) DO UPDATE
      SET sender_peer_id=EXCLUDED.sender_peer_id,
          review_reason=EXCLUDED.review_reason,
          identity_evidence=EXCLUDED.identity_evidence
      WHERE lifecycle_review_events.status='pending'
    RETURNING id INTO v_id;
    IF v_id IS NULL THEN
        SELECT id INTO v_id FROM lifecycle_review_events
        WHERE raw_id=p_raw_id AND lifecycle_term=p_term
          AND candidate_username=COALESCE(NULLIF(lower(btrim(p_candidate_username)),''),'');
    END IF;
    RETURN v_id;
END
$fn$;

CREATE OR REPLACE FUNCTION extract_lifecycle_review_enqueue(
    p_raw_id BIGINT, p_rule_version_id BIGINT, p_sender_peer_id BIGINT,
    p_term VARCHAR(64), p_candidate_username VARCHAR(128), p_reason VARCHAR(64)
) RETURNS BIGINT
LANGUAGE sql AS $fn$
    SELECT extract_lifecycle_review_enqueue($1,$2,$3,$4,$5,$6,'{}'::jsonb)
$fn$;

CREATE OR REPLACE FUNCTION lifecycle_review_resolve(
    p_event_id BIGINT, p_action VARCHAR(16), p_listing_id BIGINT,
    p_actor VARCHAR(128), p_reason TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE v_event lifecycle_review_events%ROWTYPE; v_username VARCHAR(128);
BEGIN
    SELECT * INTO v_event FROM lifecycle_review_events WHERE id=p_event_id FOR UPDATE;
    IF NOT FOUND OR v_event.status <> 'pending' THEN RETURN FALSE; END IF;
    IF p_action NOT IN ('approve','reject') THEN RAISE EXCEPTION 'invalid lifecycle review action'; END IF;
    IF p_action='approve' THEN
        IF p_listing_id IS NULL THEN RAISE EXCEPTION 'approval requires an explicit listing_id'; END IF;
        IF v_event.candidate_username = '' THEN RAISE EXCEPTION 'approval requires an explicit Telegram identity'; END IF;
        SELECT contact_username INTO v_username FROM listings WHERE id=p_listing_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'listing not found'; END IF;
        IF lower(COALESCE(v_username,'')) <> v_event.candidate_username THEN
            RAISE EXCEPTION 'selected listing does not match the candidate Telegram username';
        END IF;
        UPDATE listings SET status='archived',updated_at=NOW()
        WHERE id=p_listing_id AND status='published';
        IF NOT FOUND THEN RAISE EXCEPTION 'only a published listing can be archived by lifecycle review'; END IF;
    ELSIF p_listing_id IS NOT NULL THEN
        RAISE EXCEPTION 'rejection cannot include a listing_id';
    END IF;
    UPDATE lifecycle_review_events
    SET status=CASE WHEN p_action='approve' THEN 'approved' ELSE 'rejected' END,
        review_action=p_action,resolved_listing_id=p_listing_id,
        reviewed_by=COALESCE(NULLIF(p_actor,''),'admin_api'),
        review_reason_text=COALESCE(p_reason,''),reviewed_at=NOW()
    WHERE id=p_event_id;
    RETURN TRUE;
END
$fn$;

CREATE TABLE IF NOT EXISTS rule_router_mode_events (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    previous_mode VARCHAR(12) NOT NULL CHECK (previous_mode IN ('legacy','shadow')),
    new_mode VARCHAR(12) NOT NULL CHECK (new_mode IN ('legacy','shadow')),
    actor_user_id BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rule_router_mode_events_source_time
    ON rule_router_mode_events(source_id,changed_at DESC);

CREATE TABLE IF NOT EXISTS rule_shadow_error_counts (
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    error_class VARCHAR(96) NOT NULL,
    window_started_at TIMESTAMPTZ NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 0,
    last_occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_raw_id BIGINT,
    PRIMARY KEY(source_id,error_class,window_started_at)
);
CREATE INDEX IF NOT EXISTS idx_rule_shadow_error_counts_last
    ON rule_shadow_error_counts(last_occurred_at DESC);

CREATE TABLE IF NOT EXISTS rule_shadow_cleanup_runs (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    requested_batch_size INTEGER NOT NULL,
    preview_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    deleted_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(16) NOT NULL CHECK (status IN ('running','completed','failed')),
    error_class VARCHAR(96) NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rule_shadow_cleanup_runs_started
    ON rule_shadow_cleanup_runs(started_at DESC);

COMMIT;
