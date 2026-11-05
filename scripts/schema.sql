-- nexus-edge core schema (PostgreSQL 16)
--
-- Applied automatically by the official postgres entrypoint on the FIRST
-- boot of an empty cluster (mounted read-only at
-- /docker-entrypoint-initdb.d/10-schema.sql, executed with ON_ERROR_STOP=1).
-- Safe to re-apply manually on an existing cluster:
--   docker compose exec -T nexus-db psql -U nexus -d nexus < scripts/schema.sql
-- (every CREATE uses IF NOT EXISTS; the seed insert is guarded).

-- 1. gateway account registry
CREATE TABLE IF NOT EXISTS bridge_accounts (
    id BIGSERIAL PRIMARY KEY,
    account_uid BIGINT UNIQUE,
    display_name VARCHAR(128),
    username VARCHAR(128),
    session_file VARCHAR(255) NOT NULL,
    status VARCHAR(32) DEFAULT 'active',        -- active | disconnected | needs_auth
    last_active_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. ingest sources (channels / groups)
CREATE TABLE IF NOT EXISTS ingest_sources (
    id BIGSERIAL PRIMARY KEY,
    peer_id BIGINT UNIQUE NOT NULL,
    title VARCHAR(255),
    username VARCHAR(128),
    assigned_account_id BIGINT REFERENCES bridge_accounts(id),
    last_msg_id BIGINT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ordered failover accounts for a source. FloodWait does not activate these:
-- the current request must wait for Telegram's returned cooldown.
CREATE TABLE IF NOT EXISTS ingest_source_account_backups (
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES bridge_accounts(id) ON DELETE CASCADE,
    priority INT NOT NULL DEFAULT 1 CHECK (priority > 0),
    PRIMARY KEY (source_id, account_id),
    UNIQUE (source_id, priority)
);

-- 3. raw message envelope + media metadata
CREATE TABLE IF NOT EXISTS raw_messages (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT REFERENCES ingest_sources(id),
    platform_msg_id BIGINT NOT NULL,
    sender_peer_id BIGINT,
    grouped_id BIGINT,
    raw_text TEXT,
    media_json JSONB DEFAULT '[]',
    posted_at TIMESTAMPTZ,
    UNIQUE(source_id, platform_msg_id)
);

-- 4. extraction rule configs
CREATE TABLE IF NOT EXISTS ingest_rules (
    id BIGSERIAL PRIMARY KEY,
    rule_name VARCHAR(128) NOT NULL,
    rule_type VARCHAR(24) NOT NULL DEFAULT 'listing',
    version INT DEFAULT 1,
    config_json JSONB NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE ingest_rules
    ADD COLUMN IF NOT EXISTS rule_type VARCHAR(24) NOT NULL DEFAULT 'listing';
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingest_rules_name
    ON ingest_rules(rule_name);

-- 5. rule <-> source bindings
CREATE TABLE IF NOT EXISTS source_rule_bindings (
    source_id BIGINT REFERENCES ingest_sources(id) ON DELETE CASCADE,
    rule_id BIGINT REFERENCES ingest_rules(id) ON DELETE CASCADE,
    priority INT DEFAULT 0,
    PRIMARY KEY(source_id, rule_id)
);

-- Current daily on-duty snapshots.  A business day rolls over at 04:00
-- Asia/Shanghai; expires_at makes old snapshots disappear without a job.
CREATE TABLE IF NOT EXISTS duty_snapshots (
    rule_id BIGINT REFERENCES ingest_rules(id) ON DELETE CASCADE,
    source_id BIGINT REFERENCES ingest_sources(id) ON DELETE CASCADE,
    duty_date DATE NOT NULL,
    platform_msg_id BIGINT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    contact_usernames JSONB NOT NULL DEFAULT '[]',
    green_only_usernames JSONB NOT NULL DEFAULT '[]',
    green_blue_usernames JSONB NOT NULL DEFAULT '[]',
    unresolved_links JSONB NOT NULL DEFAULT '[]',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (rule_id, source_id, duty_date)
);
CREATE INDEX IF NOT EXISTS idx_duty_snapshots_active
    ON duty_snapshots(duty_date, expires_at);
ALTER TABLE duty_snapshots
    ADD COLUMN IF NOT EXISTS green_only_usernames JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS green_blue_usernames JSONB NOT NULL DEFAULT '[]';

-- Small persistent cache for aggregate lists that point at a channel post.
CREATE TABLE IF NOT EXISTS duty_link_cache (
    target_peer_id BIGINT NOT NULL,
    target_msg_id BIGINT NOT NULL,
    contact_username VARCHAR(128) NOT NULL,
    contact_nickname VARCHAR(128),
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (target_peer_id, target_msg_id)
);
ALTER TABLE duty_link_cache ADD COLUMN IF NOT EXISTS contact_nickname VARCHAR(128);

-- Exact message/profile matches queued from the current on-duty digest.
CREATE TABLE IF NOT EXISTS duty_collection_jobs (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES ingest_rules(id) ON DELETE CASCADE,
    duty_date DATE NOT NULL,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES bridge_accounts(id),
    contact_username VARCHAR(128) NOT NULL,
    target_peer_id BIGINT NOT NULL,
    target_msg_id BIGINT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    raw_id BIGINT REFERENCES raw_messages(id) ON DELETE SET NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_duty_collection_jobs_identity
    ON duty_collection_jobs(duty_date, lower(contact_username));
CREATE INDEX IF NOT EXISTS idx_duty_collection_jobs_pending
    ON duty_collection_jobs(status, id);
ALTER TABLE duty_collection_jobs
    ADD COLUMN IF NOT EXISTS contact_peer_id BIGINT,
    ADD COLUMN IF NOT EXISTS profile_url TEXT,
    ADD COLUMN IF NOT EXISTS profile_name TEXT,
    ADD COLUMN IF NOT EXISTS profile_about TEXT,
    ADD COLUMN IF NOT EXISTS personal_channel_id BIGINT,
    ADD COLUMN IF NOT EXISTS channel_url TEXT,
    ADD COLUMN IF NOT EXISTS bot_url TEXT,
    ADD COLUMN IF NOT EXISTS pinned_msg_id BIGINT,
    ADD COLUMN IF NOT EXISTS pinned_text TEXT,
    ADD COLUMN IF NOT EXISTS profile_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS retry_count INT NOT NULL DEFAULT 0;
ALTER TABLE bridge_accounts ADD COLUMN IF NOT EXISTS duty_profile_lookup_after TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_duty_collection_recovery
    ON duty_collection_jobs(status, updated_at DESC)
    WHERE status IN ('not_found', 'failed', 'deferred');
CREATE TABLE IF NOT EXISTS duty_recovery_scans (
    id BIGSERIAL PRIMARY KEY,
    duty_job_id BIGINT NOT NULL UNIQUE REFERENCES duty_collection_jobs(id) ON DELETE CASCADE,
    scan_target INT NOT NULL DEFAULT 20 CHECK (scan_target IN (20, 50, 100)),
    scanned_count INT NOT NULL DEFAULT 0,
    scan_cursor BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'idle',
    candidates JSONB NOT NULL DEFAULT '[]',
    error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. structured listings
CREATE TABLE IF NOT EXISTS listings (
    id BIGSERIAL PRIMARY KEY,
    source_msg_id BIGINT REFERENCES raw_messages(id),
    contact_peer_id BIGINT,
    contact_username VARCHAR(128),
    nickname VARCHAR(128),
    district VARCHAR(64),
    price_amount NUMERIC(10, 2),
    price_currency VARCHAR(16) DEFAULT 'CNY',
    description TEXT,
    media_json JSONB DEFAULT '[]',
    manual_fields JSONB NOT NULL DEFAULT '{}',
    evidence_json JSONB DEFAULT '{}',
    status VARCHAR(32) DEFAULT 'pending',       -- pending | published | archived | rejected
    confidence NUMERIC(3, 2) DEFAULT 1.0,
    published_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE listings ADD COLUMN IF NOT EXISTS manual_fields JSONB NOT NULL DEFAULT '{}';
ALTER TABLE listings ADD COLUMN IF NOT EXISTS bot_url TEXT NOT NULL DEFAULT '';
ALTER TABLE listings ADD COLUMN IF NOT EXISTS custom_fields JSONB NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS listing_field_definitions (
    field_key VARCHAR(48) PRIMARY KEY,
    label VARCHAR(96) NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]',
    extraction_source VARCHAR(24) NOT NULL DEFAULT 'manual',
    required_for_publish BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE OR REPLACE FUNCTION listing_meets_publish_requirements(
    p_nickname TEXT, p_price NUMERIC, p_description TEXT, p_username TEXT,
    p_bot_url TEXT, p_media JSONB, p_custom_fields JSONB
) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $fn$
    SELECT NULLIF(BTRIM(p_nickname), '') IS NOT NULL
       AND p_price IS NOT NULL
       AND NULLIF(BTRIM(p_description), '') IS NOT NULL
       AND NULLIF(BTRIM(p_username), '') IS NOT NULL
       AND (SELECT count(*) FROM jsonb_array_elements(COALESCE(p_media, '[]'::jsonb)) item
            WHERE item->>'kind' IN ('photo','video')
              AND COALESCE(item->>'path',item->>'poster','') <> ''
              AND COALESCE(item->>'visibility','public') = 'public'
              AND COALESCE(item->>'ocr_classification','') NOT IN ('info_card','uncertain')
              AND COALESCE(item->>'ocr_review_status','not_required') <> 'pending') >= 3
       AND NOT EXISTS (
            SELECT 1 FROM listing_field_definitions d
            WHERE d.is_active AND d.required_for_publish
              AND COALESCE(NULLIF(BTRIM(p_custom_fields->>d.field_key), ''), '') = ''
       )
$fn$;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS duty_collection_job_id BIGINT
    REFERENCES duty_collection_jobs(id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_duty_collection_job
    ON listings(duty_collection_job_id)
    WHERE duty_collection_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_listings_search
    ON listings(status, district, published_at DESC);

CREATE TABLE IF NOT EXISTS listing_duplicate_decisions (
    left_listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    right_listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    decision VARCHAR(16) NOT NULL CHECK (decision IN ('keep_latest', 'keep_both')),
    decided_by BIGINT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (left_listing_id, right_listing_id),
    CHECK (left_listing_id < right_listing_id)
);

-- Admin-triggered Telegram refreshes for selected listing source messages.
CREATE TABLE IF NOT EXISTS listing_recollect_jobs (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL,
    raw_id BIGINT NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_listing_recollect_jobs_pending
    ON listing_recollect_jobs(status, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_recollect_jobs_active_listing
    ON listing_recollect_jobs(listing_id)
    WHERE status IN ('pending', 'processing');

-- 7. favorites
CREATE TABLE IF NOT EXISTS user_favorites (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    listing_id BIGINT REFERENCES listings(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, listing_id)
);

-- 8. proxy chat sessions (anti-collision isolation)
CREATE TABLE IF NOT EXISTS chat_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    listing_id BIGINT REFERENCES listings(id),
    contact_peer_id BIGINT NOT NULL,
    assigned_account_id BIGINT REFERENCES bridge_accounts(id),
    status VARCHAR(32) DEFAULT 'open',          -- open | closed
    last_msg_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- one gateway account may hold at most one open session per contact
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_single_active_session
    ON chat_sessions(assigned_account_id, contact_peer_id)
    WHERE status = 'open';

-- 9. chat messages + local outbox
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT REFERENCES chat_sessions(id) ON DELETE CASCADE,
    client_msg_id VARCHAR(64),
    platform_msg_id BIGINT,
    direction VARCHAR(8) NOT NULL,              -- in | out
    content TEXT NOT NULL,
    status VARCHAR(16) DEFAULT 'queued',        -- queued | sending | sent | failed
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbox_jobs (
    id BIGSERIAL PRIMARY KEY,
    chat_msg_id BIGINT REFERENCES chat_messages(id) ON DELETE CASCADE,
    status VARCHAR(16) DEFAULT 'pending',       -- pending | processing | completed | failed
    attempts INT DEFAULT 0,
    run_after TIMESTAMPTZ DEFAULT NOW(),
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_queue
    ON outbox_jobs(status, run_after)
    WHERE status = 'pending';

-- default extraction rule seed (constant config, insert-once guard)
INSERT INTO ingest_rules (rule_name, config_json)
SELECT 'default-rule', '{
  "gate": {"require_any_keywords": [], "blocked_sender_peer_ids": []},
  "extract": [],
  "lifecycle_terms": ["已售", "已出", "离开", "休息", "已结"]
}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM ingest_rules);

-- ============================================================
-- ingest write-path functions (called by nexus-bridge with bound
-- parameters via SELECT; keeps write SQL out of application code)
-- ============================================================

-- idempotent source registration / title refresh
CREATE OR REPLACE FUNCTION ingest_ensure_source(
    p_peer_id BIGINT,
    p_title TEXT,
    p_username TEXT
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO ingest_sources (peer_id, title, username)
    VALUES (p_peer_id, p_title, p_username)
    ON CONFLICT (peer_id) DO UPDATE SET title = EXCLUDED.title
    RETURNING id INTO v_id;
    RETURN v_id;
END
$fn$;

-- single insert point for raw messages; idempotent on
-- (source_id, platform_msg_id); returns the row id
CREATE OR REPLACE FUNCTION ingest_append_raw(
    p_source_id BIGINT,
    p_platform_msg_id BIGINT,
    p_sender_peer_id BIGINT,
    p_grouped_id BIGINT,
    p_raw_text TEXT,
    p_media_json JSONB,
    p_posted_at TIMESTAMPTZ
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO raw_messages (
        source_id, platform_msg_id, sender_peer_id,
        grouped_id, raw_text, media_json, posted_at
    ) VALUES (
        p_source_id, p_platform_msg_id, p_sender_peer_id,
        p_grouped_id, p_raw_text, p_media_json, p_posted_at
    )
    ON CONFLICT (source_id, platform_msg_id) DO NOTHING
    RETURNING id INTO v_id;
    IF v_id IS NULL THEN
        SELECT id INTO v_id
        FROM raw_messages
        WHERE source_id = p_source_id AND platform_msg_id = p_platform_msg_id;
    END IF;
    RETURN v_id;
END
$fn$;

-- forward-only source cursor advance; returns the effective cursor
CREATE OR REPLACE FUNCTION ingest_advance_source(
    p_source_id BIGINT,
    p_last_msg_id BIGINT
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_cursor BIGINT;
BEGIN
    UPDATE ingest_sources SET last_msg_id = p_last_msg_id
    WHERE id = p_source_id AND last_msg_id < p_last_msg_id
    RETURNING last_msg_id INTO v_cursor;
    IF v_cursor IS NULL THEN
        SELECT last_msg_id INTO v_cursor
        FROM ingest_sources WHERE id = p_source_id;
    END IF;
    RETURN v_cursor;
END
$fn$;

-- idempotent gateway account registration (used by the interactive
-- first-login helper); session_file doubles as the session name
CREATE OR REPLACE FUNCTION ingest_register_account(
    p_account_uid BIGINT,
    p_display_name TEXT,
    p_username TEXT,
    p_session_file TEXT
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO bridge_accounts (account_uid, display_name, username, session_file)
    VALUES (p_account_uid, p_display_name, p_username, p_session_file)
    ON CONFLICT (account_uid) DO UPDATE
        SET display_name = EXCLUDED.display_name,
            username = EXCLUDED.username,
            status = 'active',
            last_active_at = NOW()
    RETURNING id INTO v_id;
    RETURN v_id;
END
$fn$;

-- ============================================================
-- M2: extraction pipeline state + write-path functions
-- ============================================================

ALTER TABLE raw_messages
    ADD COLUMN IF NOT EXISTS extract_state VARCHAR(16) DEFAULT 'new';

ALTER TABLE raw_messages
    ADD COLUMN IF NOT EXISTS extract_claimed_at TIMESTAMPTZ;

-- upgrade the seeded default rule to the real extraction config
-- (one-time version guard; fresh clusters get v1 from the seed above
-- and are upgraded immediately by this statement in the same pass)
UPDATE ingest_rules
SET version = 2,
    config_json = $cfg$
{
  "gate": {"require_any_keywords": [], "blocked_sender_peer_ids": []},
  "extract": {
    "nickname": ["昵称[:：]\\s*([\\u4e00-\\u9fa5A-Za-z0-9_]{1,16})", "联系人[:：]\\s*([\\u4e00-\\u9fa5A-Za-z0-9_]{1,16})"],
    "username": ["@([A-Za-z0-9_]{4,32})"],
    "price": ["(?:价格|价)[:：]\\s*([0-9]+(?:\\.[0-9]{1,2})?)", "([0-9]{3,6})(?:\\s*元|\\s*块)", "[Pp]rice\\s*[:：]?\\s*([0-9]{3,6})"],
    "district": ["(?:城区|地区|区域|位置)[:：]\\s*#?([\\u4e00-\\u9fa5A-Za-z ]{1,16})", "位于([\\u4e00-\\u9fa5]{2,6})"],
    "currency_map": {"USDT": "USDT", "USD|\\$": "USD", "元|块|CNY|¥": "CNY"}
  },
  "ocr": {"enabled": true, "trigger_when_missing": ["price"]},
  "lifecycle_terms": ["已售", "已出", "离开", "休息", "已结"]
}
$cfg$::jsonb
WHERE rule_name = 'default-rule' AND version = 1;

-- claim a batch of new raw messages for extraction (row-locked so
-- multiple workers, if ever added, never overlap); resolves the rule
-- config from the source binding or falls back to the default rule
CREATE OR REPLACE FUNCTION extract_claim_batch(p_limit INT)
RETURNS TABLE(
    raw_id BIGINT,
    source_id BIGINT,
    platform_msg_id BIGINT,
    sender_peer_id BIGINT,
    grouped_id BIGINT,
    raw_text TEXT,
    media_json JSONB,
    posted_at TIMESTAMPTZ,
    rule_config JSONB
)
LANGUAGE plpgsql AS $fn$
BEGIN
    RETURN QUERY
    WITH claimed AS (
        SELECT r.id
        FROM raw_messages r
        WHERE r.extract_state = 'new'
           OR (r.extract_state = 'processing'
               AND r.extract_claimed_at < NOW() - INTERVAL '10 minutes')
        ORDER BY r.id
        LIMIT GREATEST(1, p_limit)
        FOR UPDATE OF r SKIP LOCKED
    )
    UPDATE raw_messages rm
    SET extract_state = 'processing', extract_claimed_at = NOW()
    FROM claimed c
    WHERE rm.id = c.id
    RETURNING rm.id, rm.source_id, rm.platform_msg_id, rm.sender_peer_id,
              rm.grouped_id, rm.raw_text, rm.media_json, rm.posted_at,
              COALESCE(
                  (SELECT ir.config_json
                   FROM source_rule_bindings b
                   JOIN ingest_rules ir ON ir.id = b.rule_id
                       AND ir.is_active AND ir.rule_type = 'listing'
                   WHERE b.source_id = rm.source_id
                   ORDER BY b.priority DESC, ir.id
                   LIMIT 1),
                  (SELECT ir.config_json
                   FROM ingest_rules ir
                   WHERE ir.rule_name = 'default-rule' AND ir.is_active
                   LIMIT 1)
              ) AS rule_config;
END
$fn$;

-- terminal state marker: done (listing created or nothing to extract)
-- or dropped (gate / lifecycle / empty extraction)
CREATE OR REPLACE FUNCTION extract_mark(
    p_raw_id BIGINT,
    p_state TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE raw_messages
    SET extract_state = p_state
    WHERE id = p_raw_id AND extract_state = 'processing';
    RETURN FOUND;
END
$fn$;

-- create (or reuse) the structured listing for a raw message; the
-- evidence jsonb records where each field came from (regex / ocr)
CREATE OR REPLACE FUNCTION extract_create_listing(
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
    p_confidence NUMERIC
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    SELECT id INTO v_id FROM listings
    WHERE source_msg_id = p_source_msg_id LIMIT 1;
    IF v_id IS NOT NULL THEN
        RETURN v_id;
    END IF;
    INSERT INTO listings (
        source_msg_id, contact_peer_id, contact_username, nickname,
        district, price_amount, price_currency, description,
        media_json, evidence_json, confidence
    ) VALUES (
        p_source_msg_id, p_contact_peer_id, p_contact_username, p_nickname,
        p_district, p_price_amount, p_price_currency, p_description,
        p_media_json, p_evidence_json, p_confidence
    )
    RETURNING id INTO v_id;
    RETURN v_id;
END
$fn$;

-- lifecycle interception: archive every published listing bound to the
-- given contact; returns the number of archived cards (0 = noise drop)
CREATE OR REPLACE FUNCTION extract_archive_for_contact(
    p_contact_peer_id BIGINT,
    p_contact_username TEXT
) RETURNS INTEGER
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INTEGER := 0;
BEGIN
    IF p_contact_peer_id IS NOT NULL THEN
        UPDATE listings
        SET status = 'archived', updated_at = NOW()
        WHERE status = 'published' AND contact_peer_id = p_contact_peer_id;
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    IF v_count = 0 AND p_contact_username IS NOT NULL THEN
        UPDATE listings
        SET status = 'archived', updated_at = NOW()
        WHERE status = 'published' AND contact_username = p_contact_username;
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;
    RETURN v_count;
END
$fn$;

-- ============================================================
-- M3: review workflow + public square + favorites
-- ============================================================

-- "发布时间"展示的是消息本身的发送时间（或人工上架时的消息时间），
-- 不是采集/上架操作发生的时间
CREATE OR REPLACE FUNCTION listings_publish(p_id BIGINT) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE listings
    SET status = 'published', updated_at = NOW(),
        published_at = COALESCE(
            (SELECT rm.posted_at FROM raw_messages rm
             WHERE rm.id = listings.source_msg_id),
            NOW())
    WHERE id = p_id AND status <> 'archived';
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION listings_reject(p_id BIGINT) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE listings
    SET status = 'rejected', updated_at = NOW()
    WHERE id = p_id AND status = 'pending';
    RETURN FOUND;
END
$fn$;

-- reviewer field corrections; NULL keeps the stored value
CREATE OR REPLACE FUNCTION listings_update_fields(
    p_id BIGINT,
    p_nickname TEXT,
    p_district TEXT,
    p_price_amount NUMERIC,
    p_price_currency TEXT,
    p_description TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE listings SET
        nickname = COALESCE(p_nickname, nickname),
        district = COALESCE(p_district, district),
        price_amount = COALESCE(p_price_amount, price_amount),
        price_currency = COALESCE(p_price_currency, price_currency),
        description = COALESCE(p_description, description),
        updated_at = NOW()
    WHERE id = p_id;
    RETURN FOUND;
END
$fn$;

-- toggle favorite for a local user id; returns TRUE when now favorited
CREATE OR REPLACE FUNCTION favorite_toggle(
    p_user_id BIGINT,
    p_listing_id BIGINT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE
    v_added BOOLEAN;
BEGIN
    INSERT INTO user_favorites (user_id, listing_id)
    VALUES (p_user_id, p_listing_id)
    ON CONFLICT (user_id, listing_id) DO NOTHING;
    v_added := FOUND;
    IF NOT v_added THEN
        DELETE FROM user_favorites
        WHERE user_id = p_user_id AND listing_id = p_listing_id;
    END IF;
    RETURN v_added;
END
$fn$;

-- review workbench queue: listing joined with its source message and
-- source title (mother-message pane); DROP first because OR REPLACE
-- cannot change the return structure of an existing function
DROP FUNCTION IF EXISTS review_queue(TEXT, INT, INT);
CREATE OR REPLACE FUNCTION review_queue(
    p_status TEXT,
    p_limit INT,
    p_offset INT
) RETURNS TABLE(
    id BIGINT, nickname VARCHAR(128), district VARCHAR(64),
    price_amount NUMERIC, price_currency VARCHAR(16),
    description TEXT, media_json JSONB,
    evidence_json JSONB, confidence NUMERIC, status VARCHAR(32),
    contact_peer_id BIGINT, contact_username VARCHAR(128),
    created_at TIMESTAMPTZ, raw_text TEXT, source_title VARCHAR(255),
    raw_posted_at TIMESTAMPTZ
)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT l.id, l.nickname, l.district, l.price_amount, l.price_currency,
           l.description, l.media_json, l.evidence_json, l.confidence,
           l.status, l.contact_peer_id, l.contact_username, l.created_at,
           rm.raw_text, s.title, rm.posted_at
    FROM listings l
    LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
    LEFT JOIN ingest_sources s ON s.id = rm.source_id
    WHERE l.status = p_status
    ORDER BY l.id DESC
    LIMIT GREATEST(1, p_limit) OFFSET GREATEST(0, p_offset);
END
$fn$;

-- ============================================================
-- M4: anti-collision proxy chat + local outbox
-- ============================================================

-- open (or reuse) a proxied chat session; picks the least-loaded
-- gateway account that has no open session with this contact; the
-- partial unique index (assigned_account_id, contact_peer_id) WHERE
-- status='open' is the mathematical isolation — a race loser retries
-- with the next idle account and finally returns CONFLICT_LOCKED
CREATE OR REPLACE FUNCTION chat_open_session(
    p_user_id BIGINT,
    p_listing_id BIGINT,
    p_contact_peer_id BIGINT
) RETURNS TABLE(session_id BIGINT, status TEXT, account_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_account BIGINT;
    v_sid BIGINT;
    v_tries INT := 0;
BEGIN
    SELECT cs.id, cs.assigned_account_id INTO v_sid, v_account
    FROM chat_sessions cs
    WHERE cs.user_id = p_user_id
      AND cs.contact_peer_id = p_contact_peer_id
      AND cs.status = 'open'
    LIMIT 1;
    IF v_sid IS NOT NULL THEN
        RETURN QUERY SELECT v_sid, 'open', v_account;
        RETURN;
    END IF;

    LOOP
        v_tries := v_tries + 1;
        SELECT a.id INTO v_account
        FROM bridge_accounts a
        WHERE a.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM chat_sessions cs
              WHERE cs.assigned_account_id = a.id
                AND cs.contact_peer_id = p_contact_peer_id
                AND cs.status = 'open')
        ORDER BY (
            SELECT count(*) FROM chat_sessions cs2
            WHERE cs2.assigned_account_id = a.id AND cs2.status = 'open'
        ), a.id
        LIMIT 1;
        EXIT WHEN v_account IS NULL OR v_tries > 5;
        BEGIN
            INSERT INTO chat_sessions (
                user_id, listing_id, contact_peer_id, assigned_account_id
            ) VALUES (
                p_user_id, p_listing_id, p_contact_peer_id, v_account
            )
            RETURNING id INTO v_sid;
            RETURN QUERY SELECT v_sid, 'open', v_account;
            RETURN;
        EXCEPTION WHEN unique_violation THEN
            v_sid := NULL;
        END;
    END LOOP;

    RETURN QUERY SELECT NULL::BIGINT, 'CONFLICT_LOCKED', NULL::BIGINT;
END
$fn$;

-- atomically enqueue an outbound message: chat row + outbox job
CREATE OR REPLACE FUNCTION chat_enqueue_out(
    p_session_id BIGINT,
    p_user_id BIGINT,
    p_client_msg_id TEXT,
    p_content TEXT
) RETURNS TABLE(message_id BIGINT, job_id BIGINT, status TEXT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_owner BIGINT;
    v_state TEXT;
    v_msg BIGINT;
    v_job BIGINT;
BEGIN
    SELECT cs.user_id, cs.status INTO v_owner, v_state
    FROM chat_sessions cs WHERE cs.id = p_session_id;
    IF v_owner IS NULL THEN
        RETURN QUERY SELECT NULL::BIGINT, NULL::BIGINT, 'NOT_FOUND';
        RETURN;
    END IF;
    IF v_owner <> p_user_id THEN
        RETURN QUERY SELECT NULL::BIGINT, NULL::BIGINT, 'FORBIDDEN';
        RETURN;
    END IF;
    IF v_state <> 'open' THEN
        RETURN QUERY SELECT NULL::BIGINT, NULL::BIGINT, 'CLOSED';
        RETURN;
    END IF;
    INSERT INTO chat_messages (session_id, client_msg_id, direction, content, status)
    VALUES (p_session_id, p_client_msg_id, 'out', p_content, 'queued')
    RETURNING id INTO v_msg;
    INSERT INTO outbox_jobs (chat_msg_id) VALUES (v_msg)
    RETURNING id INTO v_job;
    RETURN QUERY SELECT v_msg, v_job, 'queued';
END
$fn$;

-- store an inbound reply from a contact on its open session
CREATE OR REPLACE FUNCTION chat_incoming_store(
    p_account_id BIGINT,
    p_contact_peer_id BIGINT,
    p_platform_msg_id BIGINT,
    p_content TEXT
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_sid BIGINT; v_msg BIGINT;
BEGIN
    SELECT id INTO v_sid
    FROM chat_sessions
    WHERE assigned_account_id = p_account_id
      AND contact_peer_id = p_contact_peer_id
      AND status = 'open'
    ORDER BY last_msg_at NULLS FIRST, id
    LIMIT 1;
    IF v_sid IS NULL THEN
        RETURN NULL;
    END IF;
    INSERT INTO chat_messages (session_id, platform_msg_id, direction, content, status)
    VALUES (v_sid, p_platform_msg_id, 'in', p_content, 'sent')
    RETURNING id INTO v_msg;
    UPDATE chat_sessions SET last_msg_at = NOW() WHERE id = v_sid;
    RETURN v_msg;
END
$fn$;

-- claim one due pending job for an account (row-locked)
DROP FUNCTION IF EXISTS outbox_claim_job(BIGINT);
CREATE OR REPLACE FUNCTION outbox_claim_job(
    p_account_id BIGINT
) RETURNS TABLE(job_id BIGINT, chat_msg_id BIGINT, session_id BIGINT,
                contact_peer_id BIGINT, client_msg_id VARCHAR(64),
                content TEXT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_job BIGINT;
BEGIN
    WITH due AS (
        SELECT oj.id
        FROM outbox_jobs oj
        JOIN chat_messages cm ON cm.id = oj.chat_msg_id
        JOIN chat_sessions cs ON cs.id = cm.session_id
        WHERE oj.status = 'pending'
          AND oj.run_after <= NOW()
          AND cs.assigned_account_id = p_account_id
          AND cs.status = 'open'
        ORDER BY oj.id
        LIMIT 1
        FOR UPDATE OF oj SKIP LOCKED
    )
    UPDATE outbox_jobs o SET status = 'processing'
    FROM due WHERE o.id = due.id
    RETURNING o.id, o.chat_msg_id INTO v_job;
    IF v_job IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT oj.id, oj.chat_msg_id, cs.id, cs.contact_peer_id, cm.client_msg_id, cm.content
    FROM outbox_jobs oj
    JOIN chat_messages cm ON cm.id = oj.chat_msg_id
    JOIN chat_sessions cs ON cs.id = cm.session_id
    WHERE oj.id = v_job;
END
$fn$;

-- finish a job: completed (message sent) or retry with exponential
-- backoff (max 5 attempts, then permanently failed)
CREATE OR REPLACE FUNCTION outbox_finish_job(
    p_job_id BIGINT,
    p_ok BOOLEAN,
    p_error TEXT,
    p_platform_msg_id BIGINT
) RETURNS TEXT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_msg BIGINT; v_attempts INT;
BEGIN
    SELECT chat_msg_id, attempts INTO v_msg, v_attempts
    FROM outbox_jobs WHERE id = p_job_id AND status = 'processing';
    IF v_msg IS NULL THEN
        RETURN 'NOT_FOUND';
    END IF;
    IF p_ok THEN
        UPDATE outbox_jobs SET status = 'completed'
        WHERE id = p_job_id;
        UPDATE chat_messages
        SET status = 'sent', platform_msg_id = COALESCE(p_platform_msg_id, platform_msg_id)
        WHERE id = v_msg;
        UPDATE chat_sessions cs
        SET last_msg_at = NOW()
        WHERE cs.id = (SELECT session_id FROM chat_messages WHERE id = v_msg);
        RETURN 'completed';
    END IF;
    v_attempts := v_attempts + 1;
    IF v_attempts >= 5 THEN
        UPDATE outbox_jobs SET status = 'failed', attempts = v_attempts,
               last_error = p_error
        WHERE id = p_job_id;
        UPDATE chat_messages SET status = 'failed' WHERE id = v_msg;
        RETURN 'failed';
    END IF;
    UPDATE outbox_jobs
    SET status = 'pending', attempts = v_attempts,
        last_error = p_error,
        run_after = NOW() + (POWER(2, v_attempts) * 5) * INTERVAL '1 second'
    WHERE id = p_job_id;
    RETURN 'retry';
END
$fn$;

-- message history for the owning user
DROP FUNCTION IF EXISTS chat_history(BIGINT, BIGINT, INT);
CREATE OR REPLACE FUNCTION chat_history(
    p_session_id BIGINT,
    p_user_id BIGINT,
    p_limit INT
) RETURNS TABLE(id BIGINT, direction VARCHAR, content TEXT, status VARCHAR,
                client_msg_id VARCHAR, created_at TIMESTAMPTZ,
                error_text VARCHAR(500), attempts INT, channel VARCHAR(16))
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT m.id, m.direction, m.content, m.status, m.client_msg_id, m.created_at
    FROM chat_messages m
    JOIN chat_sessions cs ON cs.id = m.session_id
    WHERE m.session_id = p_session_id AND cs.user_id = p_user_id
    ORDER BY m.id
    LIMIT GREATEST(1, p_limit);
END
$fn$;

-- ============================================================
-- M5: rule workshop (CRUD, binding, dry-run samples)
-- ============================================================

DROP FUNCTION IF EXISTS rule_upsert(VARCHAR, JSONB, BOOLEAN);
DROP FUNCTION IF EXISTS rule_upsert(VARCHAR, VARCHAR, JSONB, BOOLEAN);
CREATE OR REPLACE FUNCTION rule_upsert(
    p_rule_name VARCHAR(128),
    p_rule_type VARCHAR(24),
    p_config JSONB,
    p_active BOOLEAN
) RETURNS TABLE(rule_id BIGINT, version INT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT; v_ver INT;
BEGIN
    SELECT ir.id, ir.version INTO v_id, v_ver
    FROM ingest_rules ir WHERE ir.rule_name = p_rule_name;
    IF v_id IS NULL THEN
        INSERT INTO ingest_rules AS ir (rule_name, rule_type, version, config_json, is_active)
        VALUES (p_rule_name, p_rule_type, 1, p_config, p_active)
        RETURNING ir.id, ir.version INTO v_id, v_ver;
    ELSE
        UPDATE ingest_rules ir
        SET version = ir.version + 1, rule_type = p_rule_type,
            config_json = p_config, is_active = p_active
        WHERE ir.id = v_id
        RETURNING ir.id, ir.version INTO v_id, v_ver;
    END IF;
    RETURN QUERY SELECT v_id, v_ver;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_delete(p_rule_id BIGINT) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    DELETE FROM ingest_rules WHERE id = p_rule_id AND rule_name <> 'default-rule';
    RETURN FOUND;
END
$fn$;

DROP FUNCTION IF EXISTS rule_list();
CREATE OR REPLACE FUNCTION rule_list()
RETURNS TABLE(rule_id BIGINT, rule_name VARCHAR(128), rule_type VARCHAR(24), version INT,
              config_json JSONB, is_active BOOLEAN, created_at TIMESTAMPTZ,
              bindings JSONB)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT ir.id, ir.rule_name, ir.rule_type, ir.version, ir.config_json, ir.is_active,
           ir.created_at,
           COALESCE((
               SELECT jsonb_agg(jsonb_build_object(
                          'source_id', b.source_id,
                          'title', s.title,
                          'priority', b.priority
                      ) ORDER BY b.priority DESC)
               FROM source_rule_bindings b
               JOIN ingest_sources s ON s.id = b.source_id
               WHERE b.rule_id = ir.id
           ), '[]'::jsonb)
    FROM ingest_rules ir ORDER BY ir.id;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_bind_source(
    p_rule_id BIGINT,
    p_source_id BIGINT,
    p_priority INT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO source_rule_bindings (source_id, rule_id, priority)
    VALUES (p_source_id, p_rule_id, p_priority)
    ON CONFLICT (source_id, rule_id) DO UPDATE SET priority = EXCLUDED.priority;
    RETURN TRUE;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_unbind_source(
    p_rule_id BIGINT,
    p_source_id BIGINT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    DELETE FROM source_rule_bindings
    WHERE rule_id = p_rule_id AND source_id = p_source_id;
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION duty_snapshot_upsert(
    p_rule_id BIGINT,
    p_source_id BIGINT,
    p_duty_date DATE,
    p_platform_msg_id BIGINT,
    p_content_hash VARCHAR(64),
    p_contact_usernames JSONB,
    p_unresolved_links JSONB,
    p_green_only_usernames JSONB,
    p_green_blue_usernames JSONB,
    p_observed_at TIMESTAMPTZ,
    p_expires_at TIMESTAMPTZ
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE
    v_changed BOOLEAN := FALSE;
BEGIN
    INSERT INTO duty_snapshots AS ds (
        rule_id, source_id, duty_date, platform_msg_id, content_hash,
        contact_usernames, unresolved_links, green_only_usernames,
        green_blue_usernames, observed_at, expires_at
    ) VALUES (
        p_rule_id, p_source_id, p_duty_date, p_platform_msg_id, p_content_hash,
        p_contact_usernames, p_unresolved_links, p_green_only_usernames,
        p_green_blue_usernames, p_observed_at, p_expires_at
    )
    ON CONFLICT (rule_id, source_id, duty_date) DO UPDATE SET
        platform_msg_id = EXCLUDED.platform_msg_id,
        content_hash = EXCLUDED.content_hash,
        contact_usernames = CASE
            WHEN jsonb_array_length(EXCLUDED.contact_usernames) = 0
             AND jsonb_array_length(EXCLUDED.unresolved_links) > 0
                THEN ds.contact_usernames
            ELSE EXCLUDED.contact_usernames
        END,
        green_only_usernames = CASE
            WHEN jsonb_array_length(EXCLUDED.contact_usernames) = 0
             AND jsonb_array_length(EXCLUDED.unresolved_links) > 0
                THEN ds.green_only_usernames
            ELSE EXCLUDED.green_only_usernames
        END,
        green_blue_usernames = CASE
            WHEN jsonb_array_length(EXCLUDED.contact_usernames) = 0
             AND jsonb_array_length(EXCLUDED.unresolved_links) > 0
                THEN ds.green_blue_usernames
            ELSE EXCLUDED.green_blue_usernames
        END,
        unresolved_links = EXCLUDED.unresolved_links,
        observed_at = EXCLUDED.observed_at,
        expires_at = EXCLUDED.expires_at
    WHERE ds.content_hash IS DISTINCT FROM EXCLUDED.content_hash
       OR ds.platform_msg_id IS DISTINCT FROM EXCLUDED.platform_msg_id
    RETURNING TRUE INTO v_changed;
    RETURN COALESCE(v_changed, FALSE);
END
$fn$;

DROP FUNCTION IF EXISTS duty_link_cache_upsert(BIGINT, BIGINT, VARCHAR(128));
CREATE OR REPLACE FUNCTION duty_link_cache_upsert(
    p_target_peer_id BIGINT,
    p_target_msg_id BIGINT,
    p_contact_username VARCHAR(128),
    p_contact_nickname VARCHAR(128)
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
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

-- recent raw messages as dry-run samples (with source title)
DROP FUNCTION IF EXISTS rules_dryrun_samples(INT);
CREATE OR REPLACE FUNCTION rules_dryrun_samples(
    p_limit INT
) RETURNS TABLE(raw_id BIGINT, raw_text TEXT, media_json JSONB,
                source_title VARCHAR(255))
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT rm.id, rm.raw_text, rm.media_json, s.title
    FROM raw_messages rm
    LEFT JOIN ingest_sources s ON s.id = rm.source_id
    WHERE rm.raw_text IS NOT NULL AND length(trim(rm.raw_text)) > 0
    ORDER BY rm.id DESC
    LIMIT GREATEST(1, p_limit);
END
$fn$;

-- samples from one specific source (rule tuning per group)
CREATE OR REPLACE FUNCTION rules_samples_for_source(
    p_source_id BIGINT,
    p_limit INT
) RETURNS TABLE(raw_id BIGINT, raw_text TEXT, source_title VARCHAR(255))
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT rm.id, rm.raw_text, s.title
    FROM raw_messages rm
    JOIN ingest_sources s ON s.id = rm.source_id
    WHERE rm.source_id = p_source_id
      AND rm.raw_text IS NOT NULL AND length(trim(rm.raw_text)) > 0
    ORDER BY rm.id DESC
    LIMIT GREATEST(1, p_limit);
END
$fn$;

-- ============================================================
-- M10: app settings (LLM config in UI) + manual withdraw
-- ============================================================

CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(64) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION settings_upsert(
    p_key VARCHAR(64),
    p_value TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO app_settings AS st (key, value, updated_at)
    VALUES (p_key, p_value, NOW())
    ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value, updated_at = NOW();
    RETURN TRUE;
END
$fn$;

CREATE OR REPLACE FUNCTION settings_get(
    p_key VARCHAR(64)
) RETURNS TEXT
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN (SELECT st.value FROM app_settings st WHERE st.key = p_key);
END
$fn$;

-- manual withdraw of a published listing (same terminal state as the
-- lifecycle auto-archive)
CREATE OR REPLACE FUNCTION listings_withdraw(p_id BIGINT) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE listings l
    SET status = 'archived', updated_at = NOW()
    WHERE l.id = p_id AND l.status = 'published';
    RETURN FOUND;
END
$fn$;

-- ============================================================
-- M11: history backfill as rule-tuning samples
-- ============================================================

CREATE TABLE IF NOT EXISTS backfill_jobs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT REFERENCES ingest_sources(id),
    account_id BIGINT,
    peer_id BIGINT NOT NULL,
    msg_limit INT DEFAULT 50,
    job_mode VARCHAR(12) DEFAULT 'history',  -- history | media
    status VARCHAR(16) DEFAULT 'pending',   -- pending|processing|done|failed
    fetched INT DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE backfill_jobs DROP COLUMN IF EXISTS mode;
ALTER TABLE backfill_jobs ADD COLUMN IF NOT EXISTS job_mode VARCHAR(12) DEFAULT 'history';

-- sample insert: history backfill goes through the FULL extraction
-- pipeline (auto-publish applies); ON CONFLICT keeps old rows as-is
-- so re-running a backfill never resurrects deleted cards.
-- Signature changed (media_json added): DROP first.
DROP FUNCTION IF EXISTS ingest_append_sample(BIGINT, BIGINT, BIGINT, BIGINT, TEXT, TIMESTAMPTZ);
CREATE OR REPLACE FUNCTION ingest_append_sample(
    p_source_id BIGINT,
    p_platform_msg_id BIGINT,
    p_sender_peer_id BIGINT,
    p_grouped_id BIGINT,
    p_raw_text TEXT,
    p_posted_at TIMESTAMPTZ,
    p_media_json JSONB
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO raw_messages (
        source_id, platform_msg_id, sender_peer_id, grouped_id,
        raw_text, posted_at, media_json, extract_state
    ) VALUES (
        p_source_id, p_platform_msg_id, p_sender_peer_id, p_grouped_id,
        p_raw_text, p_posted_at, COALESCE(p_media_json, '[]'::jsonb), 'new'
    )
    ON CONFLICT (source_id, platform_msg_id) DO NOTHING
    RETURNING id INTO v_id;
    RETURN v_id;   -- NULL when the message already existed
END
$fn$;

-- attach media to an ALREADY stored raw message (media backfill);
-- also surfaces the media on its listing card when the card has none
CREATE OR REPLACE FUNCTION ingest_set_media(
    p_raw_id BIGINT,
    p_media_json JSONB
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE raw_messages r
    SET media_json = p_media_json
    WHERE r.id = p_raw_id
      AND COALESCE(jsonb_array_length(r.media_json), 0) = 0
      AND COALESCE(jsonb_array_length(p_media_json), 0) > 0;
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    UPDATE listings l
    SET media_json = p_media_json, updated_at = NOW()
    WHERE l.source_msg_id = p_raw_id
      AND COALESCE(jsonb_array_length(l.media_json), 0) = 0;
    RETURN TRUE;
END
$fn$;

-- after media lands, requeue extracted cards that still lack a price
-- so the OCR pass can read it off the newly fetched images
CREATE OR REPLACE FUNCTION ingest_reset_missing_price(
    p_source_id BIGINT
) RETURNS INT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INT;
BEGIN
    UPDATE raw_messages r
    SET extract_state = 'new'
    WHERE r.source_id = p_source_id
      AND r.extract_state = 'done'
      AND COALESCE(jsonb_array_length(r.media_json), 0) > 0
      AND EXISTS (
          SELECT 1 FROM listings l
          WHERE l.source_msg_id = r.id AND l.price_amount IS NULL
      );
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

DROP FUNCTION IF EXISTS backfill_enqueue(BIGINT, BIGINT, BIGINT, INT);
DROP FUNCTION IF EXISTS backfill_enqueue(BIGINT, BIGINT, BIGINT, INT, VARCHAR);
CREATE OR REPLACE FUNCTION backfill_enqueue(
    p_source_id BIGINT,
    p_account_id BIGINT,
    p_peer_id BIGINT,
    p_limit INT,
    p_job_mode VARCHAR(12) DEFAULT 'history'
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO backfill_jobs (source_id, account_id, peer_id, msg_limit, job_mode)
    VALUES (p_source_id, p_account_id, p_peer_id,
            GREATEST(1, LEAST(p_limit, 200)), p_job_mode)
    RETURNING id INTO v_id;
    RETURN v_id;
END
$fn$;

DROP FUNCTION IF EXISTS backfill_claim();
CREATE OR REPLACE FUNCTION backfill_claim()
RETURNS TABLE(job_id BIGINT, source_id BIGINT, account_id BIGINT,
              peer_id BIGINT, msg_limit INT, job_mode VARCHAR(12))
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT; v_src BIGINT; v_acc BIGINT; v_peer BIGINT; v_lim INT; v_jmode VARCHAR(12);
BEGIN
    UPDATE backfill_jobs bj
    SET status = 'processing', updated_at = NOW()
    WHERE bj.id = (
        SELECT inner_b.id FROM backfill_jobs inner_b
        WHERE inner_b.status = 'pending'
        ORDER BY inner_b.id LIMIT 1
        FOR UPDATE OF inner_b SKIP LOCKED
    )
    RETURNING bj.id, bj.source_id, bj.account_id, bj.peer_id, bj.msg_limit, bj.job_mode
    INTO v_id, v_src, v_acc, v_peer, v_lim, v_jmode;
    IF v_id IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY SELECT v_id, v_src, v_acc, v_peer, v_lim, v_jmode;
END
$fn$;

CREATE OR REPLACE FUNCTION backfill_finish(
    p_id BIGINT,
    p_ok BOOLEAN,
    p_fetched INT,
    p_error TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE backfill_jobs bj
    SET status = CASE WHEN p_ok THEN 'done' ELSE 'failed' END,
        fetched = p_fetched,
        error = p_error,
        updated_at = NOW()
    WHERE bj.id = p_id AND bj.status = 'processing';
    RETURN FOUND;
END
$fn$;

-- batch delete of listing cards (favorites cascade; raw messages stay
-- available for re-extraction and rule samples)
CREATE OR REPLACE FUNCTION listings_batch_delete(p_ids BIGINT[])
RETURNS INT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INT;
BEGIN
    DELETE FROM listings l WHERE l.id = ANY(p_ids);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    UPDATE listing_recollect_jobs
    SET status = 'failed', error = 'card deleted', updated_at = NOW()
    WHERE listing_id = ANY(p_ids)
      AND status IN ('pending', 'processing');
    RETURN v_count;
END
$fn$;

-- ============================================================
-- M12: confidence-based auto publish + newest-wins conflict + full
-- history backfill
-- ============================================================

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS auto_published BOOLEAN DEFAULT FALSE;

-- create-or-conflict resolution: a card conflicts with an older
-- published card only when the CONTACT IDENTITY matches — same
-- username AND a matching (or missing) nickname. This prevents promo
-- links shared by different contacts from collapsing the square.
DROP FUNCTION IF EXISTS listings_upsert_newest(BIGINT, BIGINT, TEXT, TEXT, TEXT,
    NUMERIC, TEXT, TEXT, JSONB, JSONB, NUMERIC, BOOLEAN);
CREATE OR REPLACE FUNCTION listings_upsert_newest(
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
    v_id BIGINT; v_status VARCHAR; v_conflicted INT := 0;
BEGIN
    SELECT id INTO v_id FROM listings
    WHERE source_msg_id = p_source_msg_id LIMIT 1;
    IF v_id IS NOT NULL THEN
        -- re-extraction refreshes the card when it scores STRICTLY
        -- better, OR when the extraction output itself changed at equal
        -- confidence (e.g. a price_multiply rule edit turning 6 into
        -- 600): identical evidence means a no-op, so reviewer field
        -- edits are never clobbered by a plain re-run
        UPDATE listings l SET
            nickname = CASE WHEN l.manual_fields ? 'nickname' THEN l.nickname
                            ELSE COALESCE(NULLIF(p_nickname, ''), l.nickname) END,
            district = CASE WHEN l.manual_fields ? 'district' THEN l.district
                            ELSE COALESCE(NULLIF(p_district, ''), l.district) END,
            price_amount = CASE WHEN l.manual_fields ? 'price_amount' THEN l.price_amount
                                ELSE COALESCE(p_price_amount, l.price_amount) END,
            price_currency = CASE WHEN l.manual_fields ? 'price_currency' THEN l.price_currency
                                  ELSE COALESCE(NULLIF(p_price_currency, ''), l.price_currency) END,
            description = CASE WHEN l.manual_fields ? 'description' THEN l.description
                               ELSE COALESCE(NULLIF(p_description, ''), l.description) END,
            evidence_json = COALESCE(p_evidence_json, l.evidence_json),
            media_json = CASE WHEN l.manual_fields ? 'media' THEN l.media_json
                              WHEN COALESCE(jsonb_array_length(p_media_json), 0) > 0
                              THEN p_media_json ELSE l.media_json END,
            confidence = GREATEST(l.confidence, p_confidence),
            published_at = CASE WHEN l.status = 'published'
                                THEN COALESCE(p_posted_at, l.published_at)
                                ELSE l.published_at END,
            updated_at = NOW()
        WHERE l.id = v_id
          AND (p_confidence > l.confidence
               OR (p_confidence = l.confidence
                   AND p_evidence_json IS DISTINCT FROM l.evidence_json));
        RETURN QUERY SELECT v_id,
            (SELECT l.status FROM listings l WHERE l.id = v_id), 0;
        RETURN;
    END IF;

    -- newest-wins: archive prior PUBLISHED cards of the same contact.
    -- Identity key is the TG username (case-insensitive) — nicknames
    -- are display text and vary between groups ("王佳怡，" vs
    -- "王佳怡"); the same peer id is an equally strong alias.
    UPDATE listings l
    SET status = 'archived', updated_at = NOW()
    WHERE l.status = 'published'
      AND (
            (p_contact_username IS NOT NULL
             AND lower(l.contact_username) = lower(p_contact_username))
         OR (p_contact_peer_id IS NOT NULL
             AND l.contact_peer_id = p_contact_peer_id
             AND l.contact_peer_id <> 0)
          );
    GET DIAGNOSTICS v_conflicted = ROW_COUNT;

    v_status := CASE WHEN p_publish THEN 'published' ELSE 'pending' END;
    INSERT INTO listings (
        source_msg_id, contact_peer_id, contact_username, nickname,
        district, price_amount, price_currency, description,
        media_json, evidence_json, confidence, status,
        auto_published, published_at
    ) VALUES (
        p_source_msg_id, p_contact_peer_id, p_contact_username, p_nickname,
        p_district, p_price_amount, p_price_currency, p_description,
        p_media_json, p_evidence_json, p_confidence, v_status,
        p_publish, CASE WHEN p_publish THEN COALESCE(p_posted_at, NOW()) END
    )
    RETURNING id INTO v_id;
    RETURN QUERY SELECT v_id, v_status, v_conflicted;
END
$fn$;

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

-- re-run extraction for every processed message of one source; used
-- after a rule is improved so existing cards pick up the new regexes
-- (upsert's better-confidence branch refreshes them in place)
CREATE OR REPLACE FUNCTION ingest_requeue_source(p_source_id BIGINT)
RETURNS INTEGER
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INTEGER := 0;
BEGIN
    UPDATE raw_messages rm
    SET extract_state = 'new', extract_claimed_at = NOW()
    WHERE rm.source_id = p_source_id
      AND rm.extract_state = 'done'
      AND EXISTS (SELECT 1 FROM source_rule_bindings b
                  WHERE b.source_id = p_source_id);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

-- one-time migration: cards published before the posted_at change carry
-- the collection timestamp; align them with their message's real time
CREATE OR REPLACE FUNCTION listings_sync_published_at()
RETURNS INTEGER
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INTEGER := 0;
BEGIN
    UPDATE listings l
    SET published_at = rm.posted_at, updated_at = NOW()
    FROM raw_messages rm
    WHERE rm.id = l.source_msg_id
      AND l.status = 'published'
      AND l.published_at IS DISTINCT FROM rm.posted_at;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

-- one-time migration: strip a captured leading "@" from stored contact
-- usernames (idempotent; a second replay touches 0 rows)
UPDATE listings SET contact_username = ltrim(contact_username, '@')
WHERE contact_username LIKE '@%';
UPDATE chat_sessions SET contact_username = ltrim(contact_username, '@')
WHERE contact_username LIKE '@%';

-- one-time cleanup: when several PUBLISHED cards share one TG username
-- keep the newest and archive the rest (idempotent on replay)
CREATE OR REPLACE FUNCTION listings_dedup_same_username()
RETURNS INTEGER
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INTEGER := 0;
BEGIN
    UPDATE listings l
    SET status = 'archived', updated_at = NOW()
    WHERE l.status = 'published'
      AND l.contact_username IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM listings newer
          WHERE newer.status = 'published'
            AND lower(newer.contact_username) = lower(l.contact_username)
            AND (newer.published_at, newer.id) > (l.published_at, l.id));
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

-- numeric setting helper (thresholds stored in app_settings)
CREATE OR REPLACE FUNCTION settings_get_num(
    p_key VARCHAR(64),
    p_fallback NUMERIC
) RETURNS NUMERIC
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN COALESCE(
        (SELECT st.value::NUMERIC FROM app_settings st WHERE st.key = p_key),
        p_fallback
    );
END
$fn$;

-- full backfill: one job per active bound source
CREATE OR REPLACE FUNCTION backfill_enqueue_all_sources(
    p_limit INT
) RETURNS INT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INT := 0;
BEGIN
    INSERT INTO backfill_jobs (source_id, account_id, peer_id, msg_limit)
    SELECT s.id, s.assigned_account_id, s.peer_id, GREATEST(1, LEAST(p_limit, 200))
    FROM ingest_sources s
    WHERE s.is_active
      AND s.assigned_account_id IS NOT NULL
      AND (
          NOT EXISTS (
              SELECT 1 FROM source_rule_bindings rb
              JOIN ingest_rules ir ON ir.id = rb.rule_id
              WHERE rb.source_id = s.id AND ir.is_active
                AND ir.rule_type = 'on_duty'
          )
          OR EXISTS (
              SELECT 1 FROM source_rule_bindings rb
              JOIN ingest_rules ir ON ir.id = rb.rule_id
              WHERE rb.source_id = s.id AND ir.is_active
                AND ir.rule_type = 'listing'
          )
      )
      AND NOT EXISTS (
          SELECT 1 FROM backfill_jobs b
          WHERE b.source_id = s.id AND b.status IN ('pending', 'processing')
      );
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

-- ============================================================
-- M6: publicly visible contact presence (no auth flow, per spec cut)
-- ============================================================

CREATE TABLE IF NOT EXISTS contact_presence (
    contact_peer_id BIGINT PRIMARY KEY,
    is_online BOOLEAN NOT NULL DEFAULT FALSE,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION presence_upsert(
    p_contact_peer_id BIGINT,
    p_is_online BOOLEAN
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO contact_presence AS cp (contact_peer_id, is_online, checked_at)
    VALUES (p_contact_peer_id, p_is_online, NOW())
    ON CONFLICT (contact_peer_id) DO UPDATE
        SET is_online = EXCLUDED.is_online, checked_at = NOW()
        WHERE cp.checked_at < NOW();  -- idempotent no-op guard for replays
    RETURN TRUE;
END
$fn$;

-- ============================================================
-- M7: contact bio penetration chain with manual join gate
-- ============================================================

CREATE TABLE IF NOT EXISTS contact_probe (
    id BIGSERIAL PRIMARY KEY,
    contact_peer_id BIGINT NOT NULL,
    bio_text TEXT,
    candidate_links JSONB DEFAULT '[]',
    status VARCHAR(24) DEFAULT 'pending',   -- pending|approved|rejected|processing|fetched|failed
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- enqueue a contact bio probe; idempotent while an open probe exists
CREATE OR REPLACE FUNCTION probe_enqueue(
    p_contact_peer_id BIGINT,
    p_bio TEXT,
    p_links JSONB
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    SELECT id INTO v_id FROM contact_probe
    WHERE contact_peer_id = p_contact_peer_id
      AND status IN ('pending', 'approved', 'processing')
    LIMIT 1;
    IF v_id IS NOT NULL THEN
        RETURN NULL;
    END IF;
    INSERT INTO contact_probe (contact_peer_id, bio_text, candidate_links)
    VALUES (p_contact_peer_id, p_bio, p_links)
    RETURNING id INTO v_id;
    RETURN v_id;
END
$fn$;

CREATE OR REPLACE FUNCTION probe_queue(
    p_status VARCHAR(24)
) RETURNS TABLE(id BIGINT, contact_peer_id BIGINT, bio_text TEXT,
                candidate_links JSONB, status VARCHAR(24), created_at TIMESTAMPTZ)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT cp.id, cp.contact_peer_id, cp.bio_text, cp.candidate_links,
           cp.status, cp.created_at
    FROM contact_probe cp
    WHERE cp.status = p_status
    ORDER BY cp.id;
END
$fn$;

-- manual safety gate: only pending rows can be approved/rejected
CREATE OR REPLACE FUNCTION probe_decide(
    p_id BIGINT,
    p_decision VARCHAR(24)
) RETURNS VARCHAR
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INT;
BEGIN
    IF p_decision NOT IN ('approved', 'rejected') THEN
        RETURN 'BAD_DECISION';
    END IF;
    UPDATE contact_probe cp
    SET status = p_decision, updated_at = NOW()
    WHERE cp.id = p_id AND cp.status = 'pending';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN CASE WHEN v_count = 1 THEN p_decision ELSE 'NOT_FOUND' END;
END
$fn$;

-- bridge claims one approved probe (row-locked)
CREATE OR REPLACE FUNCTION probe_claim_approved()
RETURNS TABLE(id BIGINT, contact_peer_id BIGINT, candidate_links JSONB)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
    v_contact BIGINT;
    v_links JSONB;
BEGIN
    UPDATE contact_probe cp
    SET status = 'processing', updated_at = NOW()
    WHERE cp.id = (
        SELECT inner_p.id FROM contact_probe inner_p
        WHERE inner_p.status = 'approved'
        ORDER BY inner_p.id LIMIT 1
        FOR UPDATE OF inner_p SKIP LOCKED
    )
    RETURNING cp.id, cp.contact_peer_id, cp.candidate_links
    INTO v_id, v_contact, v_links;
    IF v_id IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY SELECT v_id, v_contact, v_links;
END
$fn$;

CREATE OR REPLACE FUNCTION probe_mark(
    p_id BIGINT,
    p_status VARCHAR(24)
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE contact_probe cp
    SET status = p_status, updated_at = NOW()
    WHERE cp.id = p_id AND cp.status = 'processing';
    RETURN FOUND;
END
$fn$;

-- ============================================================
-- M8: session lifecycle (manual + idle close, read receipts)
-- ============================================================

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS last_read_msg_id BIGINT DEFAULT 0;

-- owner closes an open session, releasing the gateway account
CREATE OR REPLACE FUNCTION chat_close_session(
    p_session_id BIGINT,
    p_user_id BIGINT
) RETURNS TEXT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INT;
BEGIN
    UPDATE chat_sessions cs
    SET status = 'closed'
    WHERE cs.id = p_session_id AND cs.user_id = p_user_id AND cs.status = 'open';
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN CASE WHEN v_count = 1 THEN 'closed' ELSE 'NOT_FOUND' END;
END
$fn$;

-- read receipt: remember the newest message id the user has seen
CREATE OR REPLACE FUNCTION chat_mark_read(
    p_session_id BIGINT,
    p_user_id BIGINT,
    p_last_read_msg_id BIGINT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE chat_sessions cs
    SET last_read_msg_id = GREATEST(cs.last_read_msg_id, p_last_read_msg_id)
    WHERE cs.id = p_session_id AND cs.user_id = p_user_id;
    RETURN FOUND;
END
$fn$;

-- auto-close sessions idle longer than the threshold; keeps accounts
-- circulating in the anti-collision pool; returns closed count
CREATE OR REPLACE FUNCTION maintenance_close_stale_sessions(
    p_max_idle_hours INT
) RETURNS INT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INT;
BEGIN
    UPDATE chat_sessions cs
    SET status = 'closed'
    WHERE cs.status = 'open'
      AND COALESCE(cs.last_msg_at, cs.created_at)
          < NOW() - (GREATEST(1, p_max_idle_hours) * INTERVAL '1 hour');
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

-- session list with unread badge count (return structure changed: DROP first)
DROP FUNCTION IF EXISTS chat_sessions_for_user(BIGINT);
CREATE OR REPLACE FUNCTION chat_sessions_for_user(
    p_user_id BIGINT
) RETURNS TABLE(session_id BIGINT, contact_peer_id BIGINT,
                contact_username VARCHAR(128), account_id BIGINT,
                status VARCHAR, listing_id BIGINT, nickname VARCHAR(128),
                last_msg_at TIMESTAMPTZ, unread INT)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT cs.id, cs.contact_peer_id, cs.contact_username,
           cs.assigned_account_id, cs.status,
           cs.listing_id, l.nickname, cs.last_msg_at,
           (SELECT count(*)::INT FROM chat_messages m
            WHERE m.session_id = cs.id AND m.direction = 'in'
              AND m.id > cs.last_read_msg_id) AS unread_count
    FROM chat_sessions cs
    LEFT JOIN listings l ON l.id = cs.listing_id
    WHERE cs.user_id = p_user_id
    ORDER BY cs.last_msg_at DESC NULLS LAST, cs.id DESC;
END
$fn$;

-- ============================================================
-- M9: source management (group/channel discovery + binding)
-- ============================================================

-- dialogs visible to a gateway account, refreshed by the bridge
CREATE TABLE IF NOT EXISTS dialog_cache (
    account_id BIGINT REFERENCES bridge_accounts(id) ON DELETE CASCADE,
    peer_id BIGINT NOT NULL,
    title VARCHAR(255),
    username VARCHAR(128),
    kind VARCHAR(16),               -- group | channel | chat
    refreshed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (account_id, peer_id)
);

CREATE OR REPLACE FUNCTION dialog_upsert(
    p_account_id BIGINT,
    p_peer_id BIGINT,
    p_title VARCHAR(255),
    p_username VARCHAR(128),
    p_kind VARCHAR(16)
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO dialog_cache AS dc (account_id, peer_id, title, username, kind, refreshed_at)
    VALUES (p_account_id, p_peer_id, p_title, p_username, p_kind, NOW())
    ON CONFLICT (account_id, peer_id) DO UPDATE
        SET title = EXCLUDED.title,
            username = EXCLUDED.username,
            kind = EXCLUDED.kind,
            refreshed_at = NOW();
    RETURN TRUE;
END
$fn$;

-- assign (or reassign) a source to a gateway account
CREATE OR REPLACE FUNCTION ingest_assign_source(
    p_source_id BIGINT,
    p_account_id BIGINT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE ingest_sources s
    SET assigned_account_id = p_account_id
    WHERE s.id = p_source_id;
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION source_set_active(
    p_source_id BIGINT,
    p_is_active BOOLEAN
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE ingest_sources s
    SET is_active = p_is_active
    WHERE s.id = p_source_id;
    RETURN FOUND;
END
$fn$;

-- admin view of sources with account, cursor and collected volume
CREATE OR REPLACE FUNCTION admin_sources()
RETURNS TABLE(source_id BIGINT, peer_id BIGINT, title VARCHAR(255),
              username VARCHAR(128), is_active BOOLEAN, account_id BIGINT,
              account_name VARCHAR(128), last_msg_id BIGINT, raw_count BIGINT)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT s.id, s.peer_id, s.title, s.username, s.is_active,
           s.assigned_account_id, a.display_name,
           s.last_msg_id,
           (SELECT count(*) FROM raw_messages rm WHERE rm.source_id = s.id)
    FROM ingest_sources s
    LEFT JOIN bridge_accounts a ON a.id = s.assigned_account_id
    ORDER BY s.id;
END
$fn$;

-- ============================================================
-- M16: username-identity chat + presence tiers + account admin
-- The contact of a listing is its extracted @username (the sender is
-- a channel bot, never the contact). peer ids become a runtime cache
-- filled by presence resolution or the first outbound message.
-- ============================================================

ALTER TABLE chat_sessions ALTER COLUMN contact_peer_id DROP NOT NULL;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS contact_username VARCHAR(128);
DROP INDEX IF EXISTS idx_chat_single_active_session;
-- anti-collision: one gateway account holds at most one OPEN session
-- per contact username
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_single_active_session
    ON chat_sessions(assigned_account_id, contact_username)
    WHERE status = 'open' AND contact_username IS NOT NULL;

ALTER TABLE contact_presence ADD COLUMN IF NOT EXISTS username VARCHAR(128);
-- presence tier: online | recent (seen recently / last week) | unknown
ALTER TABLE contact_presence ADD COLUMN IF NOT EXISTS status_tier VARCHAR(16);
UPDATE contact_presence SET status_tier = 'unknown'
WHERE status_tier IS NULL AND NOT is_online;
UPDATE contact_presence SET status_tier = 'online'
WHERE status_tier IS NULL AND is_online;

CREATE TABLE IF NOT EXISTS login_requests (
    id BIGSERIAL PRIMARY KEY,
    phone VARCHAR(32) NOT NULL,
    session_name VARCHAR(64) NOT NULL,
    state VARCHAR(24) DEFAULT 'pending',   -- pending | running | code_sent | password_needed | done | failed | cancelled
    code VARCHAR(16),
    password TEXT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- open (or reuse) a proxied chat session keyed by contact username
DROP FUNCTION IF EXISTS chat_open_session(BIGINT, BIGINT, BIGINT);
CREATE OR REPLACE FUNCTION chat_open_session(
    p_user_id BIGINT,
    p_listing_id BIGINT,
    p_contact_username VARCHAR(128),
    p_contact_peer_id BIGINT
) RETURNS TABLE(session_id BIGINT, status TEXT, account_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_account BIGINT;
    v_sid BIGINT;
    v_tries INT := 0;
BEGIN
    SELECT cs.id, cs.assigned_account_id INTO v_sid, v_account
    FROM chat_sessions cs
    WHERE cs.user_id = p_user_id
      AND cs.status = 'open'
      AND (
            (p_contact_username IS NOT NULL
             AND cs.contact_username = p_contact_username)
         OR (p_contact_username IS NULL
             AND p_contact_peer_id IS NOT NULL
             AND cs.contact_peer_id = p_contact_peer_id)
          )
    LIMIT 1;
    IF v_sid IS NOT NULL THEN
        RETURN QUERY SELECT v_sid, 'open', v_account;
        RETURN;
    END IF;

    LOOP
        v_tries := v_tries + 1;
        SELECT a.id INTO v_account
        FROM bridge_accounts a
        WHERE a.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM chat_sessions cs
              WHERE cs.assigned_account_id = a.id
                AND cs.status = 'open'
                AND (
                      (p_contact_username IS NOT NULL
                       AND cs.contact_username = p_contact_username)
                 OR (p_contact_peer_id IS NOT NULL
                     AND cs.contact_peer_id = p_contact_peer_id)
                    ))
        ORDER BY (
            SELECT count(*) FROM chat_sessions cs2
            WHERE cs2.assigned_account_id = a.id AND cs2.status = 'open'
        ), a.id
        LIMIT 1;
        EXIT WHEN v_account IS NULL OR v_tries > 5;
        BEGIN
            INSERT INTO chat_sessions (
                user_id, listing_id, contact_username,
                contact_peer_id, assigned_account_id
            ) VALUES (
                p_user_id, p_listing_id, p_contact_username,
                p_contact_peer_id, v_account
            )
            RETURNING id INTO v_sid;
            RETURN QUERY SELECT v_sid, 'open', v_account;
            RETURN;
        EXCEPTION WHEN unique_violation THEN
            v_sid := NULL;
        END;
    END LOOP;

    RETURN QUERY SELECT NULL::BIGINT, 'CONFLICT_LOCKED', NULL::BIGINT;
END
$fn$;

-- store an inbound DM on its open session, matched by peer id first
-- (precise) then by username (covers pre-resolution sessions)
DROP FUNCTION IF EXISTS chat_incoming_store(BIGINT, BIGINT, BIGINT, TEXT);
CREATE OR REPLACE FUNCTION chat_incoming_store(
    p_account_id BIGINT,
    p_contact_peer_id BIGINT,
    p_contact_username VARCHAR(128),
    p_platform_msg_id BIGINT,
    p_content TEXT
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_sid BIGINT; v_msg BIGINT;
BEGIN
    SELECT id INTO v_sid
    FROM chat_sessions
    WHERE assigned_account_id = p_account_id
      AND status = 'open'
      AND (
            (p_contact_peer_id IS NOT NULL
             AND contact_peer_id = p_contact_peer_id)
         OR (p_contact_username IS NOT NULL
             AND contact_username = p_contact_username)
          )
    ORDER BY last_msg_at NULLS FIRST, id
    LIMIT 1;
    IF v_sid IS NULL THEN
        RETURN NULL;
    END IF;
    -- dedupe by platform message id when the sender retries/reconnects
    IF p_platform_msg_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM chat_messages m
        WHERE m.session_id = v_sid
          AND m.platform_msg_id = p_platform_msg_id
          AND m.direction = 'in'
    ) THEN
        RETURN NULL;
    END IF;
    INSERT INTO chat_messages (session_id, platform_msg_id, direction, content, status)
    VALUES (v_sid, p_platform_msg_id, 'in', p_content, 'sent')
    RETURNING id INTO v_msg;
    UPDATE chat_sessions SET last_msg_at = NOW() WHERE id = v_sid;
    RETURN v_msg;
END
$fn$;

-- claim one due pending job for an account (row-locked), carrying the
-- contact username so the sender can resolve missing peer ids
DROP FUNCTION IF EXISTS outbox_claim_job(BIGINT);
CREATE OR REPLACE FUNCTION outbox_claim_job(
    p_account_id BIGINT
) RETURNS TABLE(job_id BIGINT, chat_msg_id BIGINT, session_id BIGINT,
                contact_peer_id BIGINT, contact_username VARCHAR(128),
                client_msg_id VARCHAR(64), content TEXT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_job BIGINT;
BEGIN
    WITH due AS (
        SELECT oj.id
        FROM outbox_jobs oj
        JOIN chat_messages cm ON cm.id = oj.chat_msg_id
        JOIN chat_sessions cs ON cs.id = cm.session_id
        WHERE oj.status = 'pending'
          AND oj.run_after <= NOW()
          AND cs.assigned_account_id = p_account_id
          AND cs.status = 'open'
        ORDER BY oj.id
        LIMIT 1
        FOR UPDATE OF oj SKIP LOCKED
    )
    UPDATE outbox_jobs o SET status = 'processing'
    FROM due WHERE o.id = due.id
    RETURNING o.id, o.chat_msg_id INTO v_job;
    IF v_job IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT oj.id, oj.chat_msg_id, cs.id, cs.contact_peer_id,
           cs.contact_username, cm.client_msg_id, cm.content
    FROM outbox_jobs oj
    JOIN chat_messages cm ON cm.id = oj.chat_msg_id
    JOIN chat_sessions cs ON cs.id = cm.session_id
    WHERE oj.id = v_job;
END
$fn$;

-- after the first successful outbound DM we finally know the peer id:
-- cache it on the session and on every listing of that username
CREATE OR REPLACE FUNCTION chat_bind_peer(
    p_session_id BIGINT,
    p_peer_id BIGINT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE
    v_username VARCHAR(128);
BEGIN
    SELECT cs.contact_username INTO v_username
    FROM chat_sessions cs WHERE cs.id = p_session_id;
    IF v_username IS NULL THEN
        UPDATE chat_sessions SET contact_peer_id = p_peer_id
        WHERE id = p_session_id AND contact_peer_id IS NULL;
        RETURN FOUND;
    END IF;
    UPDATE chat_sessions SET contact_peer_id = p_peer_id
    WHERE id = p_session_id;
    UPDATE listings
    SET contact_peer_id = p_peer_id
    WHERE contact_username = v_username
      AND (contact_peer_id IS NULL OR contact_peer_id <> p_peer_id);
    RETURN TRUE;
END
$fn$;

-- presence tier upsert keyed by the resolved peer id
DROP FUNCTION IF EXISTS presence_upsert(BIGINT, BOOLEAN);
CREATE OR REPLACE FUNCTION presence_upsert(
    p_contact_peer_id BIGINT,
    p_username VARCHAR(128),
    p_tier VARCHAR(16)
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    IF p_tier NOT IN ('online', 'recent', 'unknown') THEN
        RAISE EXCEPTION 'invalid presence tier %', p_tier;
    END IF;
    INSERT INTO contact_presence AS cp
        (contact_peer_id, username, status_tier, is_online, checked_at)
    VALUES (p_contact_peer_id, p_username, p_tier, p_tier = 'online', NOW())
    ON CONFLICT (contact_peer_id) DO UPDATE
        SET username = COALESCE(EXCLUDED.username, cp.username),
            status_tier = EXCLUDED.status_tier,
            is_online = EXCLUDED.is_online,
            checked_at = NOW();
    RETURN TRUE;
END
$fn$;

-- cache a resolved username -> peer id on every published listing
CREATE OR REPLACE FUNCTION presence_bind_peer(
    p_username VARCHAR(128),
    p_peer_id BIGINT
) RETURNS INTEGER
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INTEGER := 0;
BEGIN
    UPDATE listings
    SET contact_peer_id = p_peer_id
    WHERE contact_username = p_username
      AND (contact_peer_id IS NULL OR contact_peer_id <> p_peer_id);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

-- take a gateway account offline: close its open sessions, mark it
-- disconnected; bound sources keep their rows but stop collecting
CREATE OR REPLACE FUNCTION account_disconnect(p_account_id BIGINT)
RETURNS TABLE(closed_sessions INT, bound_sources Int)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_closed INT := 0; v_sources INT := 0;
BEGIN
    UPDATE chat_sessions SET status = 'closed', last_msg_at = COALESCE(last_msg_at, NOW())
    WHERE assigned_account_id = p_account_id AND status = 'open';
    GET DIAGNOSTICS v_closed = ROW_COUNT;
    SELECT count(*) INTO v_sources
    FROM ingest_sources s
    WHERE s.assigned_account_id = p_account_id AND s.is_active;
    UPDATE bridge_accounts SET status = 'disconnected', last_active_at = NOW()
    WHERE id = p_account_id;
    RETURN QUERY SELECT v_closed, v_sources;
END
$fn$;

-- admin overview of gateway accounts + the latest login attempt
CREATE OR REPLACE FUNCTION admin_accounts()
RETURNS TABLE(account_id BIGINT, account_uid BIGINT, display_name VARCHAR(128),
              username VARCHAR(128), session_file VARCHAR(255),
              status VARCHAR(32), open_sessions BIGINT,
              bound_sources BIGINT, last_active_at TIMESTAMPTZ)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT a.id, a.account_uid, a.display_name, a.username, a.session_file,
           a.status,
           (SELECT count(*) FROM chat_sessions cs
            WHERE cs.assigned_account_id = a.id AND cs.status = 'open'),
           (SELECT count(*) FROM ingest_sources s
            WHERE s.assigned_account_id = a.id),
           a.last_active_at
    FROM bridge_accounts a
    ORDER BY a.id;
END
$fn$;

-- gateway login workflow driven through the accounts admin page
CREATE OR REPLACE FUNCTION login_request_start(
    p_phone VARCHAR(32),
    p_session_name VARCHAR(64)
) RETURNS TABLE(request_id BIGINT, session_name VARCHAR(64), state VARCHAR(24))
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT; v_name VARCHAR(64);
BEGIN
    -- one live login workflow at a time
    IF EXISTS (SELECT 1 FROM login_requests lr
               WHERE lr.state IN ('pending', 'running', 'code_sent', 'password_needed')) THEN
        RAISE EXCEPTION 'a login request is already in progress';
    END IF;
    v_name := COALESCE(NULLIF(p_session_name, ''), 'gw');
    INSERT INTO login_requests AS lr (phone, session_name)
    VALUES (p_phone, v_name)
    RETURNING lr.id, lr.session_name INTO v_id, v_name;
    RETURN QUERY SELECT v_id, v_name, 'pending'::VARCHAR(24);
END
$fn$;

-- watcher claims the single pending request (state pending -> running)
CREATE OR REPLACE FUNCTION login_request_claim()
RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    UPDATE login_requests SET state = 'running', updated_at = NOW()
    WHERE id = (SELECT id FROM login_requests WHERE state = 'pending'
                ORDER BY id LIMIT 1)
    RETURNING id INTO v_id;
    RETURN v_id;
END
$fn$;

CREATE OR REPLACE FUNCTION login_request_update_state(
    p_id BIGINT, p_state VARCHAR(24), p_error TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE login_requests
    SET state = p_state, error = p_error, updated_at = NOW()
    WHERE id = p_id AND state <> 'cancelled';
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION login_request_cancel(p_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE login_requests
    SET state = 'cancelled', code = NULL, password = NULL,
        error = 'cancelled by admin', updated_at = NOW()
    WHERE id = p_id
      AND state IN ('pending', 'running', 'code_sent', 'password_needed');
    RETURN FOUND;
END
$fn$;

-- user submits the verification code / 2FA password from the admin page
CREATE OR REPLACE FUNCTION login_request_set_code(
    p_id BIGINT, p_secret TEXT, p_is_password BOOLEAN
) RETURNS TABLE(state VARCHAR(24))
LANGUAGE plpgsql AS $fn$
DECLARE
    v_state VARCHAR(24);
BEGIN
    SELECT lr.state INTO v_state FROM login_requests lr WHERE lr.id = p_id;
    IF v_state IS NULL THEN
        RAISE EXCEPTION 'login request not found';
    END IF;
    IF v_state NOT IN ('code_sent', 'password_needed', 'running') THEN
        RAISE EXCEPTION 'login request not waiting for input (state=%)', v_state;
    END IF;
    UPDATE login_requests AS lr
    SET code = CASE WHEN NOT p_is_password THEN p_secret ELSE code END,
        password = CASE WHEN p_is_password THEN p_secret ELSE password END,
        updated_at = NOW()
    WHERE lr.id = p_id AND lr.state IN ('code_sent', 'password_needed', 'running');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'login request is no longer waiting for input';
    END IF;
    RETURN QUERY SELECT v_state;
END
$fn$;

-- bridge-side helper: read the submitted secret exactly once and clear it
CREATE OR REPLACE FUNCTION login_request_take_secret(
    p_id BIGINT, p_is_password BOOLEAN
) RETURNS TEXT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_secret TEXT;
    v_state VARCHAR(24);
BEGIN
    SELECT lr.state, CASE WHEN p_is_password THEN lr.password ELSE lr.code END
    INTO v_state, v_secret FROM login_requests lr WHERE lr.id = p_id;
    IF v_state = 'cancelled' THEN
        RETURN '__NEXUS_LOGIN_CANCELLED__';
    END IF;
    IF v_secret IS NOT NULL THEN
        UPDATE login_requests
        SET code = CASE WHEN NOT p_is_password THEN NULL ELSE code END,
            password = CASE WHEN p_is_password THEN NULL ELSE password END,
            updated_at = NOW()
        WHERE id = p_id AND state <> 'cancelled';
    END IF;
    RETURN v_secret;
END
$fn$;

-- ============================================================
-- M17: multi-user site access (account gate, role from the account)
-- Passwords are stored as pbkdf2 hashes minted by nexus-core; the
-- gateway/admin role is a flag on the account itself.
-- ============================================================

CREATE TABLE IF NOT EXISTS site_users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name VARCHAR(128),
    is_admin BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION user_create(
    p_username VARCHAR(64),
    p_password_hash TEXT,
    p_display_name VARCHAR(128),
    p_is_admin BOOLEAN
) RETURNS TABLE(user_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
BEGIN
    IF NOT regexp_replace(lower(p_username), '[^a-z0-9_-]', '') = p_username
       OR char_length(p_username) < 2 THEN
        RAISE EXCEPTION 'username: 2-64 chars, letters/digits/dash/underscore only';
    END IF;
    INSERT INTO site_users (username, password_hash, display_name, is_admin)
    VALUES (p_username, p_password_hash, p_display_name, p_is_admin)
    ON CONFLICT (username) DO NOTHING
    RETURNING id INTO v_id;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'username already exists';
    END IF;
    RETURN QUERY SELECT v_id;
END
$fn$;

-- login lookup: core verifies the pbkdf2 hash in-process
CREATE OR REPLACE FUNCTION user_get_auth(p_username VARCHAR(64))
RETURNS TABLE(user_id BIGINT, password_hash TEXT, is_admin BOOLEAN, is_active BOOLEAN)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT u.id, u.password_hash, u.is_admin, u.is_active
    FROM site_users u
    WHERE lower(u.username) = lower(p_username)
    LIMIT 1;
END
$fn$;

CREATE OR REPLACE FUNCTION user_touch_login(p_user_id BIGINT) RETURNS VOID
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE site_users SET last_login_at = NOW() WHERE id = p_user_id;
END
$fn$;

CREATE OR REPLACE FUNCTION user_list()
RETURNS TABLE(user_id BIGINT, username VARCHAR(64), display_name VARCHAR(128),
              is_admin BOOLEAN, is_active BOOLEAN,
              created_at TIMESTAMPTZ, last_login_at TIMESTAMPTZ)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT u.id, u.username, u.display_name, u.is_admin, u.is_active,
           u.created_at, u.last_login_at
    FROM site_users u ORDER BY u.id;
END
$fn$;

CREATE OR REPLACE FUNCTION user_set_password(p_user_id BIGINT, p_hash TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE site_users SET password_hash = p_hash WHERE id = p_user_id;
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION user_set_admin(p_user_id BIGINT, p_is_admin BOOLEAN)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE
    v_admins INT;
BEGIN
    UPDATE site_users SET is_admin = p_is_admin WHERE id = p_user_id;
    IF NOT FOUND THEN RETURN FALSE; END IF;
    IF NOT p_is_admin THEN
        -- never strand the site without at least one active admin
        SELECT count(*) INTO v_admins
        FROM site_users WHERE is_admin AND is_active;
        IF v_admins = 0 THEN
            RAISE EXCEPTION 'cannot remove the last active admin';
        END IF;
    END IF;
    RETURN TRUE;
END
$fn$;

CREATE OR REPLACE FUNCTION user_set_active(p_user_id BIGINT, p_is_active BOOLEAN)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE
    v_admins INT;
BEGIN
    UPDATE site_users SET is_active = p_is_active WHERE id = p_user_id;
    IF NOT FOUND THEN RETURN FALSE; END IF;
    IF NOT p_is_active THEN
        SELECT count(*) INTO v_admins
        FROM site_users WHERE is_admin AND is_active;
        IF v_admins = 0 THEN
            RAISE EXCEPTION 'cannot disable the last active admin';
        END IF;
    END IF;
    RETURN TRUE;
END
$fn$;

CREATE OR REPLACE FUNCTION user_delete(p_user_id BIGINT)
RETURNS TABLE(ok BOOLEAN, open_sessions INT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_admins INT; v_open INT;
BEGIN
    SELECT count(*) INTO v_admins
    FROM site_users WHERE is_admin AND is_active AND id <> p_user_id;
    IF v_admins = 0 THEN
        RAISE EXCEPTION 'cannot delete the last active admin';
    END IF;
    SELECT count(*) INTO v_open
    FROM chat_sessions WHERE user_id = p_user_id AND status = 'open';
    UPDATE chat_sessions SET status = 'closed' WHERE user_id = p_user_id;
    DELETE FROM site_users WHERE id = p_user_id;
    RETURN QUERY SELECT TRUE, v_open;
END
$fn$;

-- M22: store enriched media (focus/score/poster) on the raw row AND
-- refresh the linked listing's media so covers and ordering update
CREATE OR REPLACE FUNCTION media_store_enriched(
    p_raw_id BIGINT,
    p_media_json JSONB
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE raw_messages SET media_json = p_media_json WHERE id = p_raw_id;
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    UPDATE listings SET media_json = p_media_json
    WHERE source_msg_id = p_raw_id;
    RETURN TRUE;
END
$fn$;

-- ============================================================
-- M23: proxied-chat delivery hardening
-- - per-message error/attempt/channel bookkeeping
-- - gateway "stranger-limit" day flag -> bot-first delivery
-- - display name backfill + first-message identity prefix support
-- ============================================================

ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS delivery_channel VARCHAR(16) DEFAULT 'direct';
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS fallback_bot VARCHAR(128);
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS error_text VARCHAR(500);
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS attempts INT DEFAULT 0;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS channel VARCHAR(16);

CREATE TABLE IF NOT EXISTS gateway_limits (
    account_id BIGINT REFERENCES bridge_accounts(id) ON DELETE CASCADE,
    day DATE NOT NULL DEFAULT CURRENT_DATE,
    mode VARCHAR(16) NOT NULL,
    PRIMARY KEY (account_id, day)
);

CREATE OR REPLACE FUNCTION gateway_limit_set(p_account_id BIGINT, p_mode VARCHAR(16))
RETURNS VOID
LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO gateway_limits (account_id, day, mode)
    VALUES (p_account_id, CURRENT_DATE, p_mode)
    ON CONFLICT (account_id, day) DO UPDATE SET mode = EXCLUDED.mode;
END
$fn$;

-- display name becomes mandatory; existing users fall back to username
UPDATE site_users SET display_name = username WHERE display_name IS NULL OR display_name = '';

-- claim gains: display name for the identity prefix, whether this is
-- the session's first outbound message, whether the contact ever
-- replied (stranger test), and the account's delivery mode for today
DROP FUNCTION IF EXISTS outbox_claim_job(BIGINT);
CREATE OR REPLACE FUNCTION outbox_claim_job(p_account_id BIGINT)
RETURNS TABLE(job_id BIGINT, chat_msg_id BIGINT, session_id BIGINT,
              contact_peer_id BIGINT, contact_username VARCHAR(128),
              client_msg_id VARCHAR(64), content TEXT,
              display_name VARCHAR(128), is_first BOOLEAN,
              has_inbound BOOLEAN, limit_mode VARCHAR(16),
              delivery_channel VARCHAR(16), fallback_bot VARCHAR(128))
LANGUAGE plpgsql AS $fn$
DECLARE
    v_job BIGINT;
BEGIN
    WITH due AS (
        SELECT oj.id
        FROM outbox_jobs oj
        JOIN chat_messages cm ON cm.id = oj.chat_msg_id
        JOIN chat_sessions cs ON cs.id = cm.session_id
        WHERE oj.status = 'pending'
          AND oj.run_after <= NOW()
          AND cs.assigned_account_id = p_account_id
          AND cs.status = 'open'
        ORDER BY oj.id
        LIMIT 1
        FOR UPDATE OF oj SKIP LOCKED
    )
    UPDATE outbox_jobs o SET status = 'processing'
    FROM due WHERE o.id = due.id
    RETURNING o.id, o.chat_msg_id INTO v_job;
    IF v_job IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT oj.id, oj.chat_msg_id, cs.id, cs.contact_peer_id,
           cs.contact_username, cm.client_msg_id, cm.content,
           (SELECT su.display_name FROM site_users su WHERE su.id = cs.user_id),
           NOT EXISTS (SELECT 1 FROM chat_messages m2
                       WHERE m2.session_id = cs.id AND m2.direction = 'out'
                         AND m2.id <> oj.chat_msg_id),
           EXISTS (SELECT 1 FROM chat_messages m3
                   WHERE m3.session_id = cs.id AND m3.direction = 'in'),
           (SELECT gl.mode FROM gateway_limits gl
            WHERE gl.account_id = p_account_id AND gl.day = CURRENT_DATE),
           cs.delivery_channel, cs.fallback_bot
    FROM outbox_jobs oj
    JOIN chat_messages cm ON cm.id = oj.chat_msg_id
    JOIN chat_sessions cs ON cs.id = cm.session_id
    WHERE oj.id = v_job;
END
$fn$;

-- finish gains the delivery channel; retries surface attempts+error on
-- the message row so the UI can show honest progress
DROP FUNCTION IF EXISTS outbox_finish_job(BIGINT, BOOLEAN, TEXT, BIGINT);
CREATE OR REPLACE FUNCTION outbox_finish_job(
    p_job_id BIGINT,
    p_ok BOOLEAN,
    p_error TEXT,
    p_platform_msg_id BIGINT,
    p_channel VARCHAR(16)
) RETURNS TEXT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_msg BIGINT; v_attempts INT;
BEGIN
    SELECT o.chat_msg_id, o.attempts INTO v_msg, v_attempts
    FROM outbox_jobs o WHERE o.id = p_job_id AND o.status = 'processing';
    IF v_msg IS NULL THEN
        RETURN 'NOT_FOUND';
    END IF;
    IF p_ok THEN
        UPDATE outbox_jobs SET status = 'completed'
        WHERE id = p_job_id;
        UPDATE chat_messages
        SET status = 'sent', error_text = NULL,
            channel = COALESCE(p_channel, channel),
            platform_msg_id = COALESCE(p_platform_msg_id, platform_msg_id)
        WHERE id = v_msg;
        UPDATE chat_sessions cs
        SET last_msg_at = NOW()
        WHERE cs.id = (SELECT m.session_id FROM chat_messages m WHERE m.id = v_msg);
        RETURN 'completed';
    END IF;
    v_attempts := v_attempts + 1;
    IF v_attempts >= 5 THEN
        UPDATE outbox_jobs SET status = 'failed', attempts = v_attempts,
               last_error = p_error
        WHERE id = p_job_id;
        UPDATE chat_messages SET status = 'failed', attempts = v_attempts,
               error_text = left(p_error, 500)
        WHERE id = v_msg;
        RETURN 'failed';
    END IF;
    UPDATE outbox_jobs
    SET status = 'pending', attempts = v_attempts,
        last_error = p_error,
        run_after = NOW() + (POWER(2, v_attempts) * 5) * INTERVAL '1 second'
    WHERE id = p_job_id;
    UPDATE chat_messages SET attempts = v_attempts,
           error_text = left(p_error, 500)
    WHERE id = v_msg;
    RETURN 'retry';
END
$fn$;

-- admin retry: requeue a failed message; to_bot additionally pins the
-- session onto the bot delivery channel
CREATE OR REPLACE FUNCTION outbox_retry_message(
    p_session_id BIGINT,
    p_message_id BIGINT,
    p_user_id BIGINT,
    p_to_bot BOOLEAN
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE
    v_job BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM chat_sessions cs
                   WHERE cs.id = p_session_id AND cs.user_id = p_user_id) THEN
        RETURN FALSE;
    END IF;
    IF p_to_bot THEN
        UPDATE chat_sessions SET delivery_channel = 'bot'
        WHERE id = p_session_id;
    END IF;
    UPDATE outbox_jobs o
    SET status = 'pending', attempts = 0, run_after = NOW(), last_error = NULL
    FROM chat_messages m
    WHERE m.id = p_message_id AND o.chat_msg_id = m.id
      AND m.session_id = p_session_id
      AND m.status = 'failed'
    RETURNING o.id INTO v_job;
    IF v_job IS NULL THEN
        RETURN FALSE;
    END IF;
    UPDATE chat_messages SET status = 'queued', error_text = NULL, attempts = 0
    WHERE id = p_message_id;
    RETURN TRUE;
END
$fn$;

-- session list gains a preview of the last message for notifications
DROP FUNCTION IF EXISTS chat_sessions_for_user(BIGINT);
CREATE OR REPLACE FUNCTION chat_sessions_for_user(
    p_user_id BIGINT
) RETURNS TABLE(session_id BIGINT, contact_peer_id BIGINT,
                contact_username VARCHAR(128), account_id BIGINT,
                status VARCHAR, listing_id BIGINT, nickname VARCHAR(128),
                last_msg_at TIMESTAMPTZ, unread INT, last_content TEXT)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT cs.id, cs.contact_peer_id, cs.contact_username,
           cs.assigned_account_id, cs.status,
           cs.listing_id, l.nickname, cs.last_msg_at,
           (SELECT count(*)::INT FROM chat_messages m
            WHERE m.session_id = cs.id AND m.direction = 'in'
              AND m.id > cs.last_read_msg_id) AS unread_count,
           (SELECT m.content FROM chat_messages m
            WHERE m.session_id = cs.id ORDER BY m.id DESC LIMIT 1)
    FROM chat_sessions cs
    LEFT JOIN listings l ON l.id = cs.listing_id
    WHERE cs.user_id = p_user_id
    ORDER BY cs.last_msg_at DESC NULLS LAST, cs.id DESC;
END
$fn$;

-- chat history carries delivery diagnostics (admin UI shows them)
DROP FUNCTION IF EXISTS chat_history(BIGINT, BIGINT, INT);
CREATE OR REPLACE FUNCTION chat_history(
    p_session_id BIGINT,
    p_user_id BIGINT,
    p_limit INT
) RETURNS TABLE(id BIGINT, direction VARCHAR, content TEXT, status VARCHAR,
                client_msg_id VARCHAR, created_at TIMESTAMPTZ,
                error_text VARCHAR(500), attempts INT, channel VARCHAR(16))
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT m.id, m.direction, m.content, m.status, m.client_msg_id,
           m.created_at, m.error_text, m.attempts, m.channel
    FROM chat_messages m
    JOIN chat_sessions cs ON cs.id = m.session_id
    WHERE m.session_id = p_session_id AND cs.user_id = p_user_id
    ORDER BY m.id
    LIMIT GREATEST(1, p_limit);
END
$fn$;

-- outbox claim also recovers jobs wedged in 'processing' (e.g. a send
-- that never returned after a dropped connection); claiming refreshes
-- run_after so the same wedge is not re-picked for another 10 minutes
DROP FUNCTION IF EXISTS outbox_claim_job(BIGINT);
CREATE OR REPLACE FUNCTION outbox_claim_job(p_account_id BIGINT)
RETURNS TABLE(job_id BIGINT, chat_msg_id BIGINT, session_id BIGINT,
              contact_peer_id BIGINT, contact_username VARCHAR(128),
              client_msg_id VARCHAR(64), content TEXT,
              display_name VARCHAR(128), is_first BOOLEAN,
              has_inbound BOOLEAN, limit_mode VARCHAR(16),
              delivery_channel VARCHAR(16), fallback_bot VARCHAR(128))
LANGUAGE plpgsql AS $fn$
DECLARE
    v_job BIGINT;
BEGIN
    WITH due AS (
        SELECT oj.id
        FROM outbox_jobs oj
        JOIN chat_messages cm ON cm.id = oj.chat_msg_id
        JOIN chat_sessions cs ON cs.id = cm.session_id
        WHERE cs.assigned_account_id = p_account_id
          AND cs.status = 'open'
          AND (
                (oj.status = 'pending' AND oj.run_after <= NOW())
             OR (oj.status = 'processing' AND oj.run_after < NOW() - INTERVAL '10 minutes')
              )
        ORDER BY oj.id
        LIMIT 1
        FOR UPDATE OF oj SKIP LOCKED
    )
    UPDATE outbox_jobs o
    SET status = 'processing', run_after = NOW()
    FROM due WHERE o.id = due.id
    RETURNING o.id, o.chat_msg_id INTO v_job;
    IF v_job IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT oj.id, oj.chat_msg_id, cs.id, cs.contact_peer_id,
           cs.contact_username, cm.client_msg_id, cm.content,
           (SELECT su.display_name FROM site_users su WHERE su.id = cs.user_id),
           NOT EXISTS (SELECT 1 FROM chat_messages m2
                       WHERE m2.session_id = cs.id AND m2.direction = 'out'
                         AND m2.id <> oj.chat_msg_id),
           EXISTS (SELECT 1 FROM chat_messages m3
                   WHERE m3.session_id = cs.id AND m3.direction = 'in'),
           (SELECT gl.mode FROM gateway_limits gl
            WHERE gl.account_id = p_account_id AND gl.day = CURRENT_DATE),
           cs.delivery_channel, cs.fallback_bot
    FROM outbox_jobs oj
    JOIN chat_messages cm ON cm.id = oj.chat_msg_id
    JOIN chat_sessions cs ON cs.id = cm.session_id
    WHERE oj.id = v_job;
END
$fn$;

-- M24: chat media messages
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS media_kind VARCHAR(16);
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS media_path VARCHAR(512);

-- enqueue an outbound image: media travels with the row, the sender
-- reads it from the media dir
DROP FUNCTION IF EXISTS chat_enqueue_out(BIGINT, BIGINT, TEXT, TEXT);
CREATE OR REPLACE FUNCTION chat_enqueue_out(
    p_session_id BIGINT,
    p_user_id BIGINT,
    p_client_msg_id TEXT,
    p_content TEXT,
    p_media_kind VARCHAR(16),
    p_media_b64 TEXT
) RETURNS TABLE(message_id BIGINT, job_id BIGINT, status TEXT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_owner BIGINT;
    v_state TEXT;
    v_msg BIGINT;
    v_job BIGINT;
BEGIN
    SELECT cs.user_id, cs.status INTO v_owner, v_state
    FROM chat_sessions cs WHERE cs.id = p_session_id;
    IF v_owner IS NULL THEN
        RETURN QUERY SELECT NULL::BIGINT, NULL::BIGINT, 'NOT_FOUND';
        RETURN;
    END IF;
    IF v_owner <> p_user_id THEN
        RETURN QUERY SELECT NULL::BIGINT, NULL::BIGINT, 'FORBIDDEN';
        RETURN;
    END IF;
    IF v_state <> 'open' THEN
        RETURN QUERY SELECT NULL::BIGINT, NULL::BIGINT, 'CLOSED';
        RETURN;
    END IF;
    IF p_content IS NULL AND p_media_b64 IS NULL THEN
        RETURN QUERY SELECT NULL::BIGINT, NULL::BIGINT, 'EMPTY';
        RETURN;
    END IF;
    INSERT INTO chat_messages (session_id, client_msg_id, direction, content,
                               status, media_kind, media_b64)
    VALUES (p_session_id, p_client_msg_id, 'out',
            COALESCE(p_content, ''), 'queued', p_media_kind, p_media_b64)
    RETURNING id INTO v_msg;
    INSERT INTO outbox_jobs (chat_msg_id) VALUES (v_msg)
    RETURNING id INTO v_job;
    RETURN QUERY SELECT v_msg, v_job, 'queued';
END
$fn$;

-- claim carries the media for image sends
DROP FUNCTION IF EXISTS outbox_claim_job(BIGINT);
CREATE OR REPLACE FUNCTION outbox_claim_job(p_account_id BIGINT)
RETURNS TABLE(job_id BIGINT, chat_msg_id BIGINT, session_id BIGINT,
              contact_peer_id BIGINT, contact_username VARCHAR(128),
              client_msg_id VARCHAR(64), content TEXT,
              display_name VARCHAR(128), is_first BOOLEAN,
              has_inbound BOOLEAN, limit_mode VARCHAR(16),
              delivery_channel VARCHAR(16), fallback_bot VARCHAR(128),
              media_kind VARCHAR(16), media_b64 TEXT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_job BIGINT;
BEGIN
    WITH due AS (
        SELECT oj.id
        FROM outbox_jobs oj
        JOIN chat_messages cm ON cm.id = oj.chat_msg_id
        JOIN chat_sessions cs ON cs.id = cm.session_id
        WHERE cs.assigned_account_id = p_account_id
          AND cs.status = 'open'
          AND (
                (oj.status = 'pending' AND oj.run_after <= NOW())
             OR (oj.status = 'processing' AND oj.run_after < NOW() - INTERVAL '10 minutes')
              )
        ORDER BY oj.id
        LIMIT 1
        FOR UPDATE OF oj SKIP LOCKED
    )
    UPDATE outbox_jobs o
    SET status = 'processing', run_after = NOW()
    FROM due WHERE o.id = due.id
    RETURNING o.id, o.chat_msg_id INTO v_job;
    IF v_job IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT oj.id, oj.chat_msg_id, cs.id, cs.contact_peer_id,
           cs.contact_username, cm.client_msg_id, cm.content,
           (SELECT su.display_name FROM site_users su WHERE su.id = cs.user_id),
           NOT EXISTS (SELECT 1 FROM chat_messages m2
                       WHERE m2.session_id = cs.id AND m2.direction = 'out'
                         AND m2.id <> oj.chat_msg_id),
           EXISTS (SELECT 1 FROM chat_messages m3
                   WHERE m3.session_id = cs.id AND m3.direction = 'in'),
           (SELECT gl.mode FROM gateway_limits gl
            WHERE gl.account_id = p_account_id AND gl.day = CURRENT_DATE),
           cs.delivery_channel, cs.fallback_bot,
           cm.media_kind, cm.media_b64
    FROM outbox_jobs oj
    JOIN chat_messages cm ON cm.id = oj.chat_msg_id
    JOIN chat_sessions cs ON cs.id = cm.session_id
    WHERE oj.id = v_job;
END
$fn$;

-- inbound with optional photo
DROP FUNCTION IF EXISTS chat_incoming_store(BIGINT, BIGINT, VARCHAR, BIGINT, TEXT);
CREATE OR REPLACE FUNCTION chat_incoming_store(
    p_account_id BIGINT,
    p_contact_peer_id BIGINT,
    p_contact_username VARCHAR(128),
    p_platform_msg_id BIGINT,
    p_content TEXT,
    p_media_kind VARCHAR(16),
    p_media_path VARCHAR(512)
) RETURNS BIGINT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_sid BIGINT; v_msg BIGINT;
BEGIN
    SELECT id INTO v_sid
    FROM chat_sessions
    WHERE assigned_account_id = p_account_id
      AND status = 'open'
      AND (
            (p_contact_peer_id IS NOT NULL
             AND contact_peer_id = p_contact_peer_id)
         OR (p_contact_username IS NOT NULL
             AND contact_username = p_contact_username)
          )
    ORDER BY last_msg_at NULLS FIRST, id
    LIMIT 1;
    IF v_sid IS NULL THEN
        RETURN NULL;
    END IF;
    IF p_platform_msg_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM chat_messages m
        WHERE m.session_id = v_sid
          AND m.platform_msg_id = p_platform_msg_id
          AND m.direction = 'in'
    ) THEN
        RETURN NULL;
    END IF;
    INSERT INTO chat_messages (session_id, platform_msg_id, direction, content,
                               status, media_kind, media_path)
    VALUES (v_sid, p_platform_msg_id, 'in', COALESCE(p_content, ''), 'sent',
            p_media_kind, p_media_path)
    RETURNING id INTO v_msg;
    UPDATE chat_sessions SET last_msg_at = NOW() WHERE id = v_sid;
    RETURN v_msg;
END
$fn$;

-- history carries media
DROP FUNCTION IF EXISTS chat_history(BIGINT, BIGINT, INT);
CREATE OR REPLACE FUNCTION chat_history(
    p_session_id BIGINT,
    p_user_id BIGINT,
    p_limit INT
) RETURNS TABLE(id BIGINT, direction VARCHAR, content TEXT, status VARCHAR,
                client_msg_id VARCHAR, created_at TIMESTAMPTZ,
                error_text VARCHAR(500), attempts INT, channel VARCHAR(16),
                media_kind VARCHAR(16), media_path VARCHAR(512))
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    SELECT m.id, m.direction, m.content, m.status, m.client_msg_id,
           m.created_at, m.error_text, m.attempts, m.channel,
           m.media_kind, m.media_path
    FROM chat_messages m
    JOIN chat_sessions cs ON cs.id = m.session_id
    WHERE m.session_id = p_session_id AND cs.user_id = p_user_id
    ORDER BY m.id
    LIMIT GREATEST(1, p_limit);
END
$fn$;

-- outbound image bytes ride on the row (base64); the sender decodes
-- and streams them straight to Telegram without touching the disk
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS media_b64 TEXT;

-- Local OCR results and moderation state for image-based listing cards.
CREATE TABLE IF NOT EXISTS profile_collection_jobs (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES ingest_rules(id) ON DELETE CASCADE,
    duty_date DATE NOT NULL,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES bridge_accounts(id),
    contact_username VARCHAR(128) NOT NULL,
    contact_nickname VARCHAR(128),
    target_peer_id BIGINT NOT NULL,
    target_msg_id BIGINT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    next_attempt_at TIMESTAMPTZ,
    retry_count INT NOT NULL DEFAULT 0,
    error TEXT,
    stage VARCHAR(32) NOT NULL DEFAULT 'queued',
    progress_current INT NOT NULL DEFAULT 0,
    progress_total INT,
    progress_note VARCHAR(240),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE profile_collection_jobs ALTER COLUMN target_peer_id DROP NOT NULL;
ALTER TABLE profile_collection_jobs
    ADD COLUMN IF NOT EXISTS contact_nickname VARCHAR(128),
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_collection_jobs_identity
    ON profile_collection_jobs(rule_id, duty_date, lower(contact_username))
    WHERE target_msg_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_collection_jobs_message_identity
    ON profile_collection_jobs(rule_id, target_peer_id, target_msg_id, lower(contact_username))
    WHERE target_msg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_profile_collection_jobs_due
    ON profile_collection_jobs(status, next_attempt_at, id);
ALTER TABLE listings ADD COLUMN IF NOT EXISTS profile_collection_job_id BIGINT
    REFERENCES profile_collection_jobs(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_profile_collection_job
    ON listings(profile_collection_job_id)
    WHERE profile_collection_job_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS profile_collection_results (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL UNIQUE REFERENCES profile_collection_jobs(id) ON DELETE CASCADE,
    contact_peer_id BIGINT,
    contact_username VARCHAR(128) NOT NULL,
    profile_url TEXT NOT NULL DEFAULT '',
    channel_url TEXT NOT NULL DEFAULT '',
    bot_url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    media_json JSONB NOT NULL DEFAULT '[]',
    evidence_json JSONB NOT NULL DEFAULT '{}',
    profile_config JSONB NOT NULL DEFAULT '{}',
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS bot_collection_results (
    job_id BIGINT PRIMARY KEY REFERENCES profile_collection_jobs(id) ON DELETE CASCADE,
    contact_username VARCHAR(128) NOT NULL,
    profile_url TEXT NOT NULL DEFAULT '',
    bot_url TEXT NOT NULL DEFAULT '',
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
UPDATE ingest_rules
SET rule_name = 'TG号采集V2'
WHERE rule_name = '关联群组资料深采 V2'
  AND NOT EXISTS (SELECT 1 FROM ingest_rules WHERE rule_name = 'TG号采集V2');

INSERT INTO ingest_rules (rule_name, rule_type, version, config_json, is_active)
SELECT 'TG号采集V2', 'profile_collection', 1,
       '{"recent_window":10,"max_scan_messages":100,"max_photos":6,"max_videos":2,"intro_min_chars":30,"intro_min_lines":3,"channel_any_sender_matches":true,"skip_video_when_photo_found":true,"exclude_avatar":true,"pinned_followup_messages":5}'::jsonb,
       TRUE
WHERE NOT EXISTS (
    SELECT 1 FROM ingest_rules WHERE rule_name = 'TG号采集V2'
);

INSERT INTO ingest_rules (rule_name, rule_type, version, config_json, is_active)
SELECT 'Bot地址采集', 'bot_collection', 1, '{}'::jsonb, TRUE
WHERE NOT EXISTS (SELECT 1 FROM ingest_rules WHERE rule_name = 'Bot地址采集');

INSERT INTO source_rule_bindings (source_id, rule_id, priority)
SELECT b.source_id, bot_rule.id, b.priority
FROM source_rule_bindings b
JOIN ingest_rules profile_rule ON profile_rule.id = b.rule_id
                              AND profile_rule.rule_type = 'profile_collection'
JOIN ingest_rules bot_rule ON bot_rule.rule_name = 'Bot地址采集'
                           AND bot_rule.rule_type = 'bot_collection'
ON CONFLICT (source_id, rule_id) DO NOTHING;

INSERT INTO ingest_rules (rule_name, rule_type, version, config_json, is_active)
VALUES
    ('天一签到来源反查 V2', 'checkin_reverse', 1,
     '{"collection_mode":"checkin_source_reverse","source_username":"nbtgzb"}'::jsonb, TRUE),
    ('小木屋签到来源反查 V2', 'checkin_reverse', 1,
     '{"collection_mode":"checkin_source_reverse","source_username":"NBXMWBGJ"}'::jsonb, TRUE)
ON CONFLICT (rule_name) DO NOTHING;

INSERT INTO source_rule_bindings (source_id, rule_id, priority)
SELECT s.id, reverse_rule.id, 100
FROM (VALUES
    ('天一今日签到'::varchar, '天一签到来源反查 V2'::varchar),
    ('小木屋今日签到'::varchar, '小木屋签到来源反查 V2'::varchar)
) AS wanted(duty_rule_name, reverse_rule_name)
JOIN ingest_rules duty_rule ON duty_rule.rule_name = wanted.duty_rule_name
                           AND duty_rule.rule_type = 'on_duty' AND duty_rule.is_active
JOIN ingest_sources s ON s.id = (duty_rule.config_json->>'collection_source_id')::bigint
                     AND s.is_active AND s.assigned_account_id IS NOT NULL
JOIN ingest_rules reverse_rule ON reverse_rule.rule_name = wanted.reverse_rule_name
                              AND reverse_rule.is_active
ON CONFLICT (source_id, rule_id) DO NOTHING;

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

UPDATE ingest_rules
SET config_json = (config_json - 'stale_channel_days') || '{"pinned_followup_messages":5}'::jsonb
WHERE rule_type = 'profile_collection';

CREATE TABLE IF NOT EXISTS listing_media_ocr (
    id BIGSERIAL PRIMARY KEY,
    raw_id BIGINT REFERENCES raw_messages(id) ON DELETE CASCADE,
    listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
    profile_result_id BIGINT REFERENCES profile_collection_results(id) ON DELETE CASCADE,
    media_path VARCHAR(512) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    classification VARCHAR(24) NOT NULL DEFAULT 'uncertain',
    review_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    raw_text TEXT NOT NULL DEFAULT '',
    normalized_text TEXT NOT NULL DEFAULT '',
    fields_json JSONB NOT NULL DEFAULT '{}',
    evidence_json JSONB NOT NULL DEFAULT '{}',
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0,
    engine_version VARCHAR(64),
    error_text VARCHAR(500),
    attempts INT NOT NULL DEFAULT 0,
    reviewed_by BIGINT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (raw_id, media_path)
);
CREATE INDEX IF NOT EXISTS idx_listing_media_ocr_queue
    ON listing_media_ocr(review_status, status, id);
CREATE INDEX IF NOT EXISTS idx_listing_media_ocr_listing
    ON listing_media_ocr(listing_id, review_status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_media_ocr_profile_media
    ON listing_media_ocr(listing_id, media_path)
    WHERE raw_id IS NULL AND listing_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_media_ocr_profile_result_media
    ON listing_media_ocr(profile_result_id, media_path)
    WHERE profile_result_id IS NOT NULL;

CREATE OR REPLACE FUNCTION media_ocr_set_flags(
    p_raw_id BIGINT,
    p_media_path TEXT,
    p_visibility TEXT,
    p_status TEXT,
    p_classification TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE
    v_media JSONB;
BEGIN
    UPDATE raw_messages rm
    SET media_json = COALESCE((
        SELECT jsonb_agg(
            CASE WHEN item->>'path' = p_media_path
                 THEN item || jsonb_build_object(
                     'visibility', CASE
                         WHEN item->'visibility_override' = 'true'::jsonb
                             THEN item->>'visibility'
                         ELSE p_visibility
                     END,
                     'ocr_status', p_status,
                     'ocr_classification', p_classification
                 )
                 ELSE item END
            ORDER BY ord
        )
        FROM jsonb_array_elements(rm.media_json) WITH ORDINALITY AS a(item, ord)
    ), '[]'::jsonb)
    WHERE rm.id = p_raw_id
    RETURNING media_json INTO v_media;
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    UPDATE listings l SET media_json = COALESCE((
        SELECT jsonb_agg(
            CASE WHEN item->>'path' = p_media_path
                 THEN item || jsonb_build_object(
                     'visibility', CASE
                         WHEN item->'visibility_override' = 'true'::jsonb
                             THEN item->>'visibility'
                         ELSE p_visibility
                     END,
                     'ocr_status', p_status,
                     'ocr_classification', p_classification
                 )
                 ELSE item END
            ORDER BY ord
        )
        FROM jsonb_array_elements(l.media_json) WITH ORDINALITY AS a(item, ord)
    ), '[]'::jsonb), updated_at = NOW()
    WHERE l.source_msg_id = p_raw_id
      AND EXISTS (
          SELECT 1 FROM jsonb_array_elements(l.media_json) AS a(item)
          WHERE item->>'path' = p_media_path
      );
    RETURN TRUE;
END
$fn$;

CREATE TABLE IF NOT EXISTS listing_edit_history (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
    editor_user_id BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    before_json JSONB NOT NULL,
    after_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_listing_edit_history_listing
    ON listing_edit_history(listing_id, created_at DESC);
