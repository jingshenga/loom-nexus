-- Resumable public check-in-source history pool. This migration is additive;
-- it does not modify OCR review data or existing listing contents.
ALTER TABLE profile_collection_jobs
    ADD COLUMN IF NOT EXISTS contact_nickname VARCHAR(128);
ALTER TABLE profile_collection_jobs ALTER COLUMN target_peer_id DROP NOT NULL;
DROP INDEX IF EXISTS idx_profile_collection_jobs_identity;
CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_collection_jobs_identity
    ON profile_collection_jobs(rule_id, duty_date, lower(contact_username))
    WHERE target_msg_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_collection_jobs_message_identity
    ON profile_collection_jobs(rule_id, target_peer_id, target_msg_id, lower(contact_username))
    WHERE target_msg_id IS NOT NULL;

ALTER TABLE duty_link_cache ADD COLUMN IF NOT EXISTS contact_nickname VARCHAR(128);
DROP FUNCTION IF EXISTS duty_link_cache_upsert(BIGINT, BIGINT, VARCHAR(128));
CREATE OR REPLACE FUNCTION duty_link_cache_upsert(
    p_target_peer_id BIGINT, p_target_msg_id BIGINT,
    p_contact_username VARCHAR(128), p_contact_nickname VARCHAR(128)
) RETURNS BOOLEAN LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO duty_link_cache AS dlc (
        target_peer_id, target_msg_id, contact_username, contact_nickname
    ) VALUES (
        p_target_peer_id, p_target_msg_id, lower(p_contact_username), p_contact_nickname
    )
    ON CONFLICT (target_peer_id, target_msg_id) DO UPDATE SET
        contact_username = EXCLUDED.contact_username,
        contact_nickname = EXCLUDED.contact_nickname,
        resolved_at = NOW();
    RETURN TRUE;
END
$fn$;

CREATE TABLE IF NOT EXISTS checkin_history_scans (
    source_id BIGINT PRIMARY KEY REFERENCES ingest_sources(id) ON DELETE CASCADE,
    reverse_rule_id BIGINT NOT NULL REFERENCES ingest_rules(id) ON DELETE CASCADE,
    source_username VARCHAR(64) NOT NULL,
    peer_id BIGINT NOT NULL,
    cursor_msg_id BIGINT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'deferred', 'completed', 'failed')),
    scanned_count BIGINT NOT NULL DEFAULT 0,
    stored_count BIGINT NOT NULL DEFAULT 0,
    candidate_count BIGINT NOT NULL DEFAULT 0,
    retry_count INT NOT NULL DEFAULT 0,
    last_scanned_msg_id BIGINT,
    last_scanned_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error TEXT,
    progress_note VARCHAR(240) NOT NULL DEFAULT '等待历史扫描',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_checkin_history_scans_due
    ON checkin_history_scans(status, next_attempt_at, source_id);

-- Seed one resumable cursor for each active, explicitly bound reverse rule.
INSERT INTO checkin_history_scans (source_id, reverse_rule_id, source_username, peer_id)
SELECT s.id, r.id, lower(trim(both '@' FROM r.config_json->>'source_username')), s.peer_id
FROM source_rule_bindings b
JOIN ingest_sources s ON s.id = b.source_id AND s.is_active
JOIN ingest_rules r ON r.id = b.rule_id
WHERE r.rule_type = 'checkin_reverse' AND r.is_active
  AND COALESCE(r.config_json->>'source_username', '') <> ''
  AND s.peer_id IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM source_rule_bindings lb
      JOIN ingest_rules lr ON lr.id = lb.rule_id
      WHERE lb.source_id = s.id AND lr.rule_type = 'listing' AND lr.is_active
  )
ON CONFLICT (source_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS checkin_identity_index (
    listing_id BIGINT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    platform_msg_id BIGINT NOT NULL,
    contact_username VARCHAR(128) NOT NULL,
    nickname VARCHAR(128),
    posted_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_checkin_identity_username
    ON checkin_identity_index(source_id, lower(contact_username), posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_checkin_identity_nickname
    ON checkin_identity_index(source_id, lower(trim(nickname)), posted_at DESC)
    WHERE nickname IS NOT NULL AND nickname <> '';

CREATE OR REPLACE FUNCTION checkin_identity_index_upsert()
RETURNS TRIGGER LANGUAGE plpgsql AS $fn$
BEGIN
    DELETE FROM checkin_identity_index WHERE listing_id = NEW.id;
    IF NEW.contact_username IS NOT NULL AND NEW.contact_username <> '' THEN
        INSERT INTO checkin_identity_index (
            listing_id, source_id, platform_msg_id, contact_username,
            nickname, posted_at, collected_at
        )
        SELECT NEW.id, rm.source_id, rm.platform_msg_id, lower(NEW.contact_username),
               NEW.nickname, rm.posted_at, COALESCE(NEW.created_at, NOW())
        FROM raw_messages rm
        JOIN checkin_history_scans hs ON hs.source_id = rm.source_id
        WHERE rm.id = NEW.source_msg_id;
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS trg_checkin_identity_index ON listings;
CREATE TRIGGER trg_checkin_identity_index
AFTER INSERT OR UPDATE OF source_msg_id, contact_username, nickname ON listings
FOR EACH ROW EXECUTE FUNCTION checkin_identity_index_upsert();
INSERT INTO checkin_identity_index (
    listing_id, source_id, platform_msg_id, contact_username, nickname, posted_at, collected_at
)
SELECT l.id, rm.source_id, rm.platform_msg_id, lower(l.contact_username),
       l.nickname, rm.posted_at, l.created_at
FROM listings l
JOIN raw_messages rm ON rm.id = l.source_msg_id
JOIN checkin_history_scans hs ON hs.source_id = rm.source_id
WHERE l.contact_username IS NOT NULL AND l.contact_username <> ''
ON CONFLICT (listing_id) DO UPDATE SET
    source_id = EXCLUDED.source_id, platform_msg_id = EXCLUDED.platform_msg_id,
    contact_username = EXCLUDED.contact_username, nickname = EXCLUDED.nickname,
    posted_at = EXCLUDED.posted_at, collected_at = EXCLUDED.collected_at;

-- A history hit for an existing identity becomes a review candidate. It must
-- never archive the already-published card merely because an older message
-- was encountered later by the resumable scanner.
CREATE OR REPLACE FUNCTION listings_upsert_history(
    p_source_msg_id BIGINT,
    p_contact_peer_id BIGINT,
    p_contact_username TEXT,
    p_nickname TEXT,
    p_district TEXT,
    p_price_amount NUMERIC,
    p_price_currency TEXT,
    p_description TEXT,
    p_media_json JSONB,
    p_evidence_json JSONB,
    p_confidence NUMERIC,
    p_publish BOOLEAN,
    p_posted_at TIMESTAMPTZ
) RETURNS TABLE(listing_id BIGINT, status VARCHAR, conflicted INT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_existing_id BIGINT;
    v_id BIGINT;
BEGIN
    SELECT id INTO v_existing_id FROM listings
    WHERE source_msg_id = p_source_msg_id LIMIT 1;
    IF v_existing_id IS NOT NULL THEN
        RETURN QUERY SELECT * FROM listings_upsert_newest(
            p_source_msg_id, p_contact_peer_id, p_contact_username, p_nickname,
            p_district, p_price_amount, p_price_currency, p_description,
            p_media_json, p_evidence_json, p_confidence, p_publish, p_posted_at
        );
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM listings l
        WHERE l.status IN ('pending', 'duplicate_pending', 'published', 'archived')
          AND p_contact_username IS NOT NULL
          AND lower(l.contact_username) = lower(p_contact_username)
    ) THEN
        INSERT INTO listings (
            source_msg_id, contact_peer_id, contact_username, nickname,
            district, price_amount, price_currency, description,
            media_json, evidence_json, confidence, status, auto_published, published_at
        ) VALUES (
            p_source_msg_id, p_contact_peer_id, p_contact_username, p_nickname,
            p_district, p_price_amount, p_price_currency, p_description,
            p_media_json, p_evidence_json, p_confidence, 'duplicate_pending', FALSE, NULL
        ) RETURNING id INTO v_id;
        RETURN QUERY SELECT v_id, 'duplicate_pending'::varchar, 0;
        RETURN;
    END IF;

    RETURN QUERY SELECT * FROM listings_upsert_newest(
        p_source_msg_id, p_contact_peer_id, p_contact_username, p_nickname,
        p_district, p_price_amount, p_price_currency, p_description,
        p_media_json, p_evidence_json, p_confidence, p_publish, p_posted_at
    );
END
$fn$;
