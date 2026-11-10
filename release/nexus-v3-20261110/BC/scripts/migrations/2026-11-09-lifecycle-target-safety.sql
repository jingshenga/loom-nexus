-- Lifecycle messages may be posted by a source bot. Never use sender peer ID
-- as the contact target; archive only when an explicit username identifies
-- exactly one listing in the database.
BEGIN;
CREATE OR REPLACE FUNCTION extract_archive_for_contact(
    p_contact_peer_id BIGINT,
    p_contact_username TEXT
) RETURNS INTEGER
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INTEGER := 0;
BEGIN
    IF p_contact_username IS NULL OR BTRIM(p_contact_username) = '' THEN
        RETURN 0;
    END IF;

    IF (SELECT count(*) FROM listings
        WHERE lower(contact_username) = lower(BTRIM(p_contact_username))) <> 1 THEN
        RETURN 0;
    END IF;

    UPDATE listings
    SET status = 'archived', updated_at = NOW()
    WHERE status = 'published'
      AND lower(contact_username) = lower(BTRIM(p_contact_username));
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

-- Lifecycle parsing currently lacks persisted forward metadata and an
-- authorized-sender allowlist. Keep those messages reviewable without
-- granting the extractor implicit archive permission.
CREATE TABLE IF NOT EXISTS lifecycle_review_events (
    id BIGSERIAL PRIMARY KEY,
    raw_id BIGINT NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
    rule_version_id BIGINT,
    sender_peer_id BIGINT,
    lifecycle_term VARCHAR(64) NOT NULL,
    candidate_username VARCHAR(128) NOT NULL DEFAULT '',
    review_reason VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected')),
    resolved_listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
    review_action VARCHAR(16),
    review_reason_text TEXT NOT NULL DEFAULT '',
    reviewed_by VARCHAR(128),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(raw_id,lifecycle_term,candidate_username)
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_review_events_pending
    ON lifecycle_review_events(status,created_at DESC) WHERE status='pending';
ALTER TABLE lifecycle_review_events
    ADD COLUMN IF NOT EXISTS resolved_listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS review_action VARCHAR(16),
    ADD COLUMN IF NOT EXISTS review_reason_text TEXT NOT NULL DEFAULT '';

CREATE OR REPLACE FUNCTION extract_lifecycle_review_enqueue(
    p_raw_id BIGINT, p_rule_version_id BIGINT, p_sender_peer_id BIGINT,
    p_term VARCHAR(64), p_candidate_username VARCHAR(128), p_reason VARCHAR(64)
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO lifecycle_review_events(
        raw_id,rule_version_id,sender_peer_id,lifecycle_term,candidate_username,review_reason
    ) VALUES (
        p_raw_id,COALESCE(p_rule_version_id,(SELECT rule_version_id FROM raw_messages WHERE id=p_raw_id)),
        p_sender_peer_id,p_term,
        COALESCE(NULLIF(lower(btrim(p_candidate_username)),''),''),p_reason
    )
    ON CONFLICT(raw_id,lifecycle_term,candidate_username) DO UPDATE
      SET sender_peer_id=EXCLUDED.sender_peer_id,
          review_reason=EXCLUDED.review_reason
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

CREATE OR REPLACE FUNCTION lifecycle_review_resolve(
    p_event_id BIGINT, p_action VARCHAR(16), p_listing_id BIGINT,
    p_actor VARCHAR(128), p_reason TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE v_event lifecycle_review_events%ROWTYPE; v_username VARCHAR(128);
BEGIN
    SELECT * INTO v_event FROM lifecycle_review_events
    WHERE id=p_event_id FOR UPDATE;
    IF NOT FOUND OR v_event.status <> 'pending' THEN RETURN FALSE; END IF;
    IF p_action NOT IN ('approve','reject') THEN RAISE EXCEPTION 'invalid lifecycle review action'; END IF;
    IF p_action='approve' THEN
        IF p_listing_id IS NULL THEN RAISE EXCEPTION 'approval requires an explicit listing_id'; END IF;
        SELECT contact_username INTO v_username FROM listings WHERE id=p_listing_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'listing not found'; END IF;
        IF v_event.candidate_username <> '' AND lower(COALESCE(v_username,'')) <> v_event.candidate_username THEN
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
COMMIT;
