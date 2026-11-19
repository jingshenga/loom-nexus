-- Atomic intake for Telegram messages and profile collection results.
CREATE TABLE IF NOT EXISTS listing_person_groups (
    id BIGSERIAL PRIMARY KEY,
    group_key TEXT UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS listing_person_group_members (
    group_id BIGINT NOT NULL REFERENCES listing_person_groups(id) ON DELETE CASCADE,
    listing_id BIGINT NOT NULL UNIQUE REFERENCES listings(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, listing_id)
);

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS content_signature TEXT,
    ADD COLUMN IF NOT EXISTS dedup_text TEXT,
    ADD COLUMN IF NOT EXISTS source_posted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS manually_approved_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS listing_repost_evidence (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    raw_id BIGINT UNIQUE REFERENCES raw_messages(id) ON DELETE CASCADE,
    profile_job_id BIGINT UNIQUE REFERENCES profile_collection_jobs(id) ON DELETE CASCADE,
    posted_at TIMESTAMPTZ NOT NULL,
    CHECK (num_nonnulls(raw_id, profile_job_id) = 1)
);
CREATE INDEX IF NOT EXISTS idx_listing_repost_evidence_listing
    ON listing_repost_evidence(listing_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_dedup_signature
    ON listings(content_signature, last_active_at DESC) WHERE content_signature IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_listings_dedup_source_msg
    ON listings(source_msg_id) WHERE source_msg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_listings_dedup_peer
    ON listings(contact_peer_id) WHERE contact_peer_id > 0;
CREATE INDEX IF NOT EXISTS idx_listings_dedup_username
    ON listings(lower(contact_username)) WHERE contact_username IS NOT NULL;
CREATE OR REPLACE FUNCTION listing_dedup_normalize(p_text TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT btrim(regexp_replace(lower(normalize(COALESCE(p_text, ''), NFKC)),
                                '[[:space:]]+', ' ', 'g'))
$fn$;

CREATE INDEX IF NOT EXISTS idx_listings_dedup_profile
    ON listings(listing_dedup_normalize(nickname), listing_dedup_normalize(district), price_amount)
    WHERE status IN ('pending', 'published', 'duplicate_pending');

CREATE OR REPLACE FUNCTION listing_dedup_fingerprint(p_text TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT md5(NULLIF(regexp_replace(listing_dedup_normalize(p_text), '[[:space:]]+', '', 'g'), ''))
$fn$;

CREATE OR REPLACE FUNCTION listing_dedup_compare_text(p_text TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT btrim(regexp_replace(
        listing_dedup_normalize(regexp_replace(COALESCE(p_text, ''),
                    'https?://[^[:space:]]+|@[[:alnum:]_]+', ' ', 'gi')),
        '[[:punct:][:space:]]+', ' ', 'g'))
$fn$;

-- Unicode character trigrams work for unspaced Chinese as well as Latin
-- text and are independent of the PostgreSQL database locale.
CREATE OR REPLACE FUNCTION listing_dedup_trigrams(p_text TEXT) RETURNS TEXT[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT ARRAY(
        SELECT DISTINCT substr(p_text, position, 3)
        FROM generate_series(1, LEAST(length(p_text), 4096) - 2) AS offsets(position)
    )
$fn$;

CREATE OR REPLACE FUNCTION listing_dedup_similar(p_left TEXT[], p_right TEXT[]) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT shared >= 0.82 * (cardinality(p_left) + cardinality(p_right) - shared)
    FROM (
        SELECT count(*) AS shared
        FROM (SELECT unnest(p_left) INTERSECT SELECT unnest(p_right)) trigrams
    ) overlap
$fn$;

ALTER TABLE listings ADD COLUMN IF NOT EXISTS dedup_trigrams TEXT[]
    GENERATED ALWAYS AS (listing_dedup_trigrams(dedup_text)) STORED;
CREATE INDEX IF NOT EXISTS idx_listings_dedup_trigrams
    ON listings USING gin(dedup_trigrams)
    WHERE status IN ('pending', 'published', 'duplicate_pending');

CREATE OR REPLACE FUNCTION listing_same_account(
    p_peer BIGINT, p_username TEXT, p_other_peer BIGINT, p_other_username TEXT
) RETURNS BOOLEAN LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT CASE WHEN p_peer > 0 AND p_other_peer > 0 THEN p_peer = p_other_peer
                ELSE COALESCE(NULLIF(lower(p_username), '') = NULLIF(lower(p_other_username), ''), FALSE)
           END
$fn$;

UPDATE listings l
SET source_posted_at = src.posted_at,
    last_active_at = COALESCE(l.last_active_at, src.posted_at),
    content_signature = listing_dedup_fingerprint(src.body),
    dedup_text = listing_dedup_compare_text(src.body)
FROM (
    SELECT card.id, COALESCE(rm.posted_at, pc.collected_at, card.created_at, NOW()) AS posted_at,
           COALESCE(NULLIF(rm.raw_text, ''), pc.description, card.description) AS body
    FROM listings card
    LEFT JOIN raw_messages rm ON rm.id=card.source_msg_id
    LEFT JOIN profile_collection_results pc ON pc.job_id=card.profile_collection_job_id
    WHERE card.source_posted_at IS NULL
) src
WHERE l.id=src.id;

-- The same criteria feed the queue and manual-review API validation.
CREATE OR REPLACE VIEW listing_cross_account_conflicts AS
SELECT a.id AS left_id, b.id AS right_id,
       CASE WHEN profile.matches THEN '昵称、城区及资费一致'
            ELSE '原文高度相似' END AS match_reason
FROM listings a
JOIN listings b ON a.id < b.id
CROSS JOIN LATERAL (
    SELECT (NULLIF(listing_dedup_normalize(a.nickname), '') IS NOT NULL
       AND listing_dedup_normalize(a.nickname) = listing_dedup_normalize(b.nickname)
       AND NULLIF(listing_dedup_normalize(a.district), '') IS NOT NULL
       AND listing_dedup_normalize(a.district) = listing_dedup_normalize(b.district)
       AND a.price_amount = b.price_amount
       AND upper(COALESCE(a.price_currency, 'CNY')) = upper(COALESCE(b.price_currency, 'CNY'))) AS matches
) profile
LEFT JOIN listing_person_group_members ga ON ga.listing_id=a.id
LEFT JOIN listing_person_group_members gb ON gb.listing_id=b.id
LEFT JOIN listing_duplicate_decisions decision ON decision.left_listing_id=a.id AND decision.right_listing_id=b.id
WHERE a.status IN ('pending', 'published', 'duplicate_pending')
  AND b.status IN ('pending', 'published', 'duplicate_pending')
  AND decision.left_listing_id IS NULL
  AND (ga.group_id IS NULL OR gb.group_id IS NULL OR ga.group_id <> gb.group_id)
  AND (a.contact_peer_id > 0 OR b.contact_peer_id > 0
       OR NULLIF(a.contact_username, '') IS NOT NULL OR NULLIF(b.contact_username, '') IS NOT NULL)
  AND NOT listing_same_account(a.contact_peer_id, a.contact_username, b.contact_peer_id, b.contact_username)
  AND (profile.matches OR (
      length(a.dedup_text) >= 24 AND length(b.dedup_text) >= 24
      AND a.dedup_trigrams && b.dedup_trigrams
      AND listing_dedup_similar(a.dedup_trigrams, b.dedup_trigrams)
  ));

CREATE OR REPLACE FUNCTION listing_lock_intake() RETURNS VOID
LANGUAGE sql AS $fn$
    SELECT pg_advisory_xact_lock(hashtextextended('nexus:listing-intake', 0))
$fn$;

CREATE OR REPLACE FUNCTION listing_find_repost(
    p_peer BIGINT, p_username TEXT, p_nickname TEXT, p_signature TEXT, p_posted_at TIMESTAMPTZ
) RETURNS BIGINT LANGUAGE sql STABLE AS $fn$
    SELECT l.id
    FROM listings l
    WHERE l.status IN ('pending', 'published', 'duplicate_pending')
      AND l.content_signature = p_signature
      AND (p_peer > 0 OR NULLIF(p_username, '') IS NULL OR NOT EXISTS (
          SELECT 1 FROM listings conflicting
          WHERE lower(conflicting.contact_username)=lower(p_username)
            AND conflicting.contact_peer_id > 0 AND l.contact_peer_id > 0
            AND conflicting.contact_peer_id <> l.contact_peer_id
            AND conflicting.status IN ('pending', 'published', 'duplicate_pending')
      ))
      AND l.last_active_at BETWEEN p_posted_at - INTERVAL '30 days' AND p_posted_at + INTERVAL '30 days'
      AND (
          listing_same_account(p_peer, p_username, l.contact_peer_id, l.contact_username)
          OR (COALESCE(p_peer, 0) <= 0 AND NULLIF(p_username, '') IS NULL
              AND COALESCE(l.contact_peer_id, 0) <= 0 AND NULLIF(l.contact_username, '') IS NULL
              AND NULLIF(listing_dedup_normalize(p_nickname), '') IS NOT NULL
              AND listing_dedup_normalize(p_nickname) = listing_dedup_normalize(l.nickname))
      )
    ORDER BY l.source_posted_at DESC, l.id DESC
    LIMIT 1
$fn$;

CREATE OR REPLACE FUNCTION listing_attach_repost(
    p_listing BIGINT, p_raw BIGINT, p_profile_job BIGINT, p_posted_at TIMESTAMPTZ
) RETURNS VOID LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO listing_repost_evidence(listing_id, raw_id, profile_job_id, posted_at)
    VALUES (p_listing, p_raw, p_profile_job, p_posted_at)
    ON CONFLICT DO NOTHING;
    IF FOUND THEN
        UPDATE listings SET last_active_at = GREATEST(last_active_at, p_posted_at)
        WHERE id=p_listing;
    END IF;
END
$fn$;

CREATE OR REPLACE FUNCTION listing_reviewed_album(p_media JSONB) RETURNS JSONB
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT COALESCE(jsonb_agg(item ORDER BY ord), '[]'::jsonb)
    FROM jsonb_array_elements(COALESCE(p_media, '[]'::jsonb)) WITH ORDINALITY AS media(item, ord)
    WHERE item->>'kind' IN ('photo', 'video')
      AND COALESCE(item->>'path', item->>'poster', '') <> ''
      AND COALESCE(item->>'visibility', 'public') = 'public'
      AND COALESCE(item->>'ocr_classification', '') NOT IN ('info_card', 'uncertain')
      AND COALESCE(item->>'ocr_review_status', 'not_required') <> 'pending'
$fn$;

-- Record explicit approvals. Legacy profile workers also published rows
-- with auto_published=false, so that flag alone cannot vouch for them.
CREATE OR REPLACE FUNCTION listings_publish(p_id BIGINT) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE listings l SET status='published', updated_at=NOW(), auto_published=FALSE,
        published_at=COALESCE(l.source_posted_at,
            (SELECT rm.posted_at FROM raw_messages rm WHERE rm.id=l.source_msg_id), NOW()),
        manually_approved_at=NOW()
    WHERE l.id=p_id AND l.status <> 'archived';
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION listing_inherit_attributes(p_listing BIGINT, p_older BIGINT[])
RETURNS VOID LANGUAGE plpgsql AS $fn$
DECLARE
    v_new listings%ROWTYPE;
    v_old_id BIGINT;
    v_value JSONB;
    v_key TEXT;
BEGIN
    SELECT * INTO STRICT v_new FROM listings WHERE id=p_listing;
    FOREACH v_key IN ARRAY ARRAY['height_cm', 'weight_kg', 'bust_size', 'district', 'media'] LOOP
        IF v_new.manual_fields ? v_key OR (v_key IN ('height_cm','weight_kg','bust_size')
            AND (v_new.manual_fields ? 'custom_fields'
                 OR NULLIF(NULLIF(v_new.custom_fields->v_key, 'null'::jsonb), '""'::jsonb) IS NOT NULL
                 OR NULLIF(NULLIF(v_new.evidence_json->v_key->'value','null'::jsonb), '""'::jsonb) IS NOT NULL))
            OR (v_key = 'district' AND NULLIF(btrim(v_new.district), '') IS NOT NULL)
            OR (v_key = 'media' AND COALESCE(v_new.media_json, '[]'::jsonb) <> '[]'::jsonb) THEN
            CONTINUE;
        END IF;

        SELECT old.id, candidate.value INTO v_old_id, v_value
        FROM listings old
        CROSS JOIN LATERAL (
            SELECT CASE v_key
                WHEN 'district' THEN to_jsonb(NULLIF(btrim(old.district), ''))
                WHEN 'media' THEN NULLIF(listing_reviewed_album(old.media_json), '[]'::jsonb)
                ELSE COALESCE(NULLIF(old.custom_fields->v_key, 'null'::jsonb), old.evidence_json->v_key->'value')
            END AS value
        ) candidate
        WHERE old.id = ANY(p_older)
          AND NULLIF(NULLIF(candidate.value, 'null'::jsonb), '""'::jsonb) IS NOT NULL
          AND (old.manually_approved_at IS NOT NULL
               OR (old.published_at IS NOT NULL AND old.auto_published IS FALSE
                   AND old.profile_collection_job_id IS NULL AND old.duty_collection_job_id IS NULL)
               OR old.manual_fields ? v_key
               OR (v_key IN ('height_cm', 'weight_kg', 'bust_size') AND old.manual_fields ? 'custom_fields'
                   AND old.custom_fields ? v_key)
               OR old.evidence_json->v_key ? 'inherited_from')
        ORDER BY old.source_posted_at DESC NULLS LAST, old.id DESC
        LIMIT 1;

        IF FOUND THEN
            IF v_key = 'district' THEN
                v_new.district := v_value #>> '{}';
            ELSIF v_key = 'media' THEN
                v_new.media_json := v_value;
                -- Keep only source metadata here; the album lives in media_json.
                v_value := to_jsonb(jsonb_array_length(v_value));
            END IF;
            v_new.evidence_json := COALESCE(v_new.evidence_json, '{}'::jsonb)
                || jsonb_build_object(v_key, jsonb_build_object(
                    'value', v_value, 'src', 'inherited', 'inherited_from', v_old_id));
        END IF;
    END LOOP;

    UPDATE listings l SET district=v_new.district, media_json=v_new.media_json,
           evidence_json=v_new.evidence_json, updated_at=NOW()
    WHERE l.id=p_listing AND (l.district, l.media_json, l.evidence_json)
          IS DISTINCT FROM (v_new.district, v_new.media_json, v_new.evidence_json);
END
$fn$;

CREATE OR REPLACE FUNCTION listing_apply_lifecycle(p_listing BIGINT) RETURNS INT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_new listings%ROWTYPE;
    v_related BIGINT[];
    v_winner BIGINT;
    v_group BIGINT;
    v_existing_groups INT;
    v_known_peers INT;
    v_archived INT;
BEGIN
    SELECT * INTO STRICT v_new FROM listings WHERE id=p_listing FOR UPDATE;
    IF v_new.status NOT IN ('pending', 'published', 'duplicate_pending') THEN
        RETURN 0;
    END IF;

    SELECT group_id INTO v_group FROM listing_person_group_members WHERE listing_id=p_listing;
    WITH related AS (
        SELECT old.id, old.contact_peer_id, m.group_id
        FROM listings old
        LEFT JOIN listing_person_group_members m ON m.listing_id=old.id
        WHERE old.id <> p_listing
          AND old.status IN ('pending', 'published', 'duplicate_pending')
          AND ((v_new.contact_peer_id > 0 AND old.contact_peer_id = v_new.contact_peer_id)
               OR (NULLIF(v_new.contact_username, '') IS NOT NULL
                   AND lower(old.contact_username) = lower(v_new.contact_username)))
          AND listing_same_account(v_new.contact_peer_id, v_new.contact_username,
                                   old.contact_peer_id, old.contact_username)
          AND (v_group IS NULL OR m.group_id IS NULL OR m.group_id = v_group)
          AND NOT EXISTS (
              SELECT 1 FROM listing_duplicate_decisions d
              WHERE d.left_listing_id=LEAST(p_listing, old.id)
                AND d.right_listing_id=GREATEST(p_listing, old.id) AND d.decision='keep_both'
          )
        ORDER BY old.id FOR UPDATE OF old
    )
    SELECT array_agg(id ORDER BY id), count(DISTINCT group_id),
           COALESCE(v_group, min(group_id)), count(DISTINCT contact_peer_id) FILTER (WHERE contact_peer_id > 0)
    INTO v_related, v_existing_groups, v_group, v_known_peers
    FROM related;

    IF v_existing_groups > 1 OR v_known_peers > 1 THEN
        v_related := NULL;
    END IF;
    SELECT id INTO v_winner
    FROM listings WHERE id = ANY(array_prepend(p_listing, v_related))
    ORDER BY COALESCE(source_posted_at, created_at) DESC NULLS LAST, id DESC
    LIMIT 1;
    IF v_winner=p_listing AND EXISTS (
        SELECT 1 FROM listing_cross_account_conflicts WHERE left_id=p_listing OR right_id=p_listing
    ) THEN
        -- A conflicting candidate never displaces an incumbent before review.
        UPDATE listings SET status='duplicate_pending', auto_published=FALSE, published_at=NULL, updated_at=NOW()
        WHERE id=p_listing AND (status='pending' OR (status='published' AND auto_published));
        RETURN 0;
    END IF;
    UPDATE listings SET status='pending', updated_at=NOW()
    WHERE id=p_listing AND status='duplicate_pending';
    IF v_related IS NULL THEN
        RETURN 0;
    END IF;
    PERFORM listing_inherit_attributes(v_winner, array_remove(array_prepend(p_listing, v_related), v_winner));
    IF v_winner=p_listing AND EXISTS (
        SELECT 1 FROM listing_cross_account_conflicts WHERE left_id=p_listing OR right_id=p_listing
    ) THEN
        UPDATE listings SET status='duplicate_pending', auto_published=FALSE, published_at=NULL, updated_at=NOW()
        WHERE id=p_listing AND (status='pending' OR (status='published' AND auto_published));
        RETURN 0;
    END IF;

    IF v_group IS NULL THEN
        INSERT INTO listing_person_groups(display_name)
        VALUES (COALESCE(v_new.nickname, '')) RETURNING id INTO v_group;
    END IF;
    INSERT INTO listing_person_group_members(group_id, listing_id)
    SELECT v_group, unnest(array_prepend(p_listing, v_related))
    ON CONFLICT (listing_id) DO NOTHING;

    UPDATE listings
    SET status='archived', updated_at=NOW(),
        evidence_json=COALESCE(evidence_json, '{}'::jsonb) || jsonb_build_object(
            'lifecycle', jsonb_build_object('reason', 'superseded_by_newer_listing',
                                            'superseded_by', v_winner))
    WHERE id = ANY(array_prepend(p_listing, v_related)) AND id<>v_winner
      AND status IN ('pending', 'published', 'duplicate_pending');
    GET DIAGNOSTICS v_archived = ROW_COUNT;
    RETURN v_archived;
END
$fn$;

CREATE OR REPLACE FUNCTION listing_intake_message(
    p_source_msg_id BIGINT, p_contact_peer_id BIGINT, p_contact_username TEXT,
    p_nickname TEXT, p_district TEXT, p_price_amount NUMERIC, p_price_currency TEXT,
    p_description TEXT, p_media_json JSONB, p_evidence_json JSONB, p_confidence NUMERIC,
    p_publish BOOLEAN, p_posted_at TIMESTAMPTZ, p_custom_fields JSONB,
    p_check_publish_requirements BOOLEAN DEFAULT TRUE
) RETURNS TABLE(listing_id BIGINT, status VARCHAR, conflicted INT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT;
    v_body TEXT;
    v_signature TEXT;
    v_posted_at TIMESTAMPTZ;
    v_archived INT := 0;
BEGIN
    -- Intake makes the fingerprint lookup, evidence attachment and supersede one
    -- transaction across worker processes and both intake paths.
    PERFORM listing_lock_intake();
    SELECT l.id INTO v_id FROM listings l WHERE l.source_msg_id=p_source_msg_id ORDER BY l.id LIMIT 1;
    IF v_id IS NOT NULL THEN
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
            custom_fields = CASE WHEN l.manual_fields ? 'custom_fields' THEN l.custom_fields
                                 ELSE l.custom_fields || COALESCE(p_custom_fields, '{}'::jsonb) END,
            evidence_json = COALESCE(p_evidence_json, '{}'::jsonb) || COALESCE((
                SELECT jsonb_object_agg(key, value)
                FROM jsonb_each(COALESCE(l.evidence_json, '{}'::jsonb))
                WHERE key='lifecycle'
                   OR (value ? 'inherited_from'
                       AND NOT (COALESCE(p_evidence_json, '{}'::jsonb) ? key)
                       AND NOT (COALESCE(p_custom_fields, '{}'::jsonb) ? key)
                       AND NOT (key='district' AND NULLIF(p_district, '') IS NOT NULL)
                       AND NOT (key='media' AND COALESCE(p_media_json, '[]'::jsonb) <> '[]'::jsonb))
            ), '{}'::jsonb),
            media_json = CASE WHEN l.manual_fields ? 'media' THEN l.media_json
                              WHEN COALESCE(p_media_json, '[]'::jsonb) <> '[]'::jsonb
                              THEN p_media_json ELSE l.media_json END,
            confidence = GREATEST(l.confidence, p_confidence),
            updated_at = NOW()
        WHERE l.id=v_id AND (p_confidence > l.confidence OR
             (p_confidence=l.confidence AND (p_evidence_json IS DISTINCT FROM l.evidence_json
                 OR COALESCE(p_custom_fields, '{}'::jsonb) <> '{}'::jsonb)));
    ELSE
        SELECT e.listing_id INTO v_id FROM listing_repost_evidence e WHERE e.raw_id=p_source_msg_id;
        IF v_id IS NULL THEN
            SELECT COALESCE(NULLIF(rm.raw_text, ''), p_description), rm.posted_at
            INTO v_body, v_posted_at FROM raw_messages rm WHERE rm.id=p_source_msg_id;
            v_body := COALESCE(v_body, p_description);
            v_posted_at := COALESCE(p_posted_at, v_posted_at, NOW());
            v_signature := listing_dedup_fingerprint(v_body);
            v_id := listing_find_repost(p_contact_peer_id, p_contact_username, p_nickname, v_signature, v_posted_at);

            IF v_id IS NOT NULL THEN
                PERFORM listing_attach_repost(v_id, p_source_msg_id, NULL, v_posted_at);
            ELSE
                INSERT INTO listings (
                    source_msg_id, contact_peer_id, contact_username, nickname, district,
                    price_amount, price_currency, description, media_json, evidence_json,
                    custom_fields, confidence, status, auto_published, published_at,
                    source_posted_at, last_active_at, content_signature, dedup_text
                ) VALUES (
                    p_source_msg_id, NULLIF(p_contact_peer_id, 0), NULLIF(p_contact_username, ''),
                    p_nickname, p_district, p_price_amount, p_price_currency, p_description,
                    COALESCE(p_media_json, '[]'::jsonb), COALESCE(p_evidence_json, '{}'::jsonb),
                    COALESCE(p_custom_fields, '{}'::jsonb), p_confidence,
                    'pending', FALSE, NULL,
                    v_posted_at, v_posted_at, v_signature, listing_dedup_compare_text(v_body)
                ) RETURNING id INTO v_id;
                v_archived := listing_apply_lifecycle(v_id);
                IF p_publish THEN
                    UPDATE listings l SET status='published', auto_published=TRUE, published_at=v_posted_at
                    WHERE l.id=v_id AND l.status='pending'
                      AND (NOT p_check_publish_requirements OR listing_meets_publish_requirements(
                          l.nickname, l.price_amount, l.description, l.contact_username,
                          l.bot_url, l.media_json, l.custom_fields));
                END IF;
            END IF;
        END IF;
    END IF;
    RETURN QUERY SELECT l.id, l.status, v_archived FROM listings l WHERE l.id=v_id;
END
$fn$;

CREATE OR REPLACE FUNCTION listings_upsert_newest(
    p_source_msg_id BIGINT, p_contact_peer_id BIGINT, p_contact_username TEXT,
    p_nickname TEXT, p_district TEXT, p_price_amount NUMERIC, p_price_currency TEXT,
    p_description TEXT, p_media_json JSONB, p_evidence_json JSONB, p_confidence NUMERIC,
    p_publish BOOLEAN, p_posted_at TIMESTAMPTZ
) RETURNS TABLE(listing_id BIGINT, status VARCHAR, conflicted INT)
LANGUAGE sql AS $fn$
    SELECT * FROM listing_intake_message($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, '{}'::jsonb, FALSE)
$fn$;

CREATE OR REPLACE FUNCTION listings_upsert_history(
    p_source_msg_id BIGINT, p_contact_peer_id BIGINT, p_contact_username TEXT,
    p_nickname TEXT, p_district TEXT, p_price_amount NUMERIC, p_price_currency TEXT,
    p_description TEXT, p_media_json JSONB, p_evidence_json JSONB, p_confidence NUMERIC,
    p_publish BOOLEAN, p_posted_at TIMESTAMPTZ
) RETURNS TABLE(listing_id BIGINT, status VARCHAR, conflicted INT)
LANGUAGE sql AS $fn$
    SELECT * FROM listings_upsert_newest($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
$fn$;

CREATE OR REPLACE FUNCTION listing_intake_profile(p_job BIGINT, p_bucket TEXT)
RETURNS BIGINT LANGUAGE plpgsql AS $fn$
DECLARE
    v_result profile_collection_results%ROWTYPE;
    v_id BIGINT;
    v_nickname TEXT;
    v_district TEXT;
    v_bot_url TEXT;
    v_evidence JSONB;
    v_signature TEXT;
    v_previous BIGINT;
BEGIN
    PERFORM listing_lock_intake();
    SELECT e.listing_id INTO v_id FROM listing_repost_evidence e WHERE e.profile_job_id=p_job;
    IF FOUND THEN RETURN v_id; END IF;
    SELECT * INTO v_result FROM profile_collection_results WHERE job_id=p_job;
    IF NOT FOUND THEN RETURN NULL; END IF;
    v_nickname := NULLIF(v_result.evidence_json->>'nickname', '');
    v_district := NULLIF(v_result.evidence_json->>'district', '');
    v_evidence := v_result.evidence_json || jsonb_build_object('review_bucket', p_bucket);
    v_signature := listing_dedup_fingerprint(v_result.description);
    SELECT id INTO v_previous FROM listings WHERE profile_collection_job_id=p_job;
    IF v_previous IS NULL THEN
        v_id := listing_find_repost(v_result.contact_peer_id, v_result.contact_username,
                                    v_nickname, v_signature, v_result.collected_at);
        IF v_id IS NOT NULL THEN
            PERFORM listing_attach_repost(v_id, NULL, p_job, v_result.collected_at);
            RETURN v_id;
        END IF;
    END IF;

    v_bot_url := COALESCE(NULLIF(btrim(v_result.bot_url), ''), (
        SELECT NULLIF(btrim(bot.bot_url), '')
        FROM bot_collection_results bot
        WHERE lower(bot.contact_username) = lower(v_result.contact_username)
          AND NULLIF(btrim(bot.bot_url), '') IS NOT NULL
        ORDER BY bot.collected_at DESC, bot.job_id DESC LIMIT 1
    ), '');
    INSERT INTO listings(
        contact_peer_id, contact_username, nickname, district, bot_url, description,
        media_json, evidence_json, status, confidence, profile_collection_job_id,
        source_posted_at, last_active_at, content_signature, dedup_text
    ) VALUES (
        v_result.contact_peer_id, v_result.contact_username, v_nickname, v_district, v_bot_url,
        v_result.description, v_result.media_json, v_evidence, 'pending', 0.95, p_job,
        v_result.collected_at, v_result.collected_at, v_signature,
        listing_dedup_compare_text(v_result.description)
    )
    ON CONFLICT (profile_collection_job_id) WHERE profile_collection_job_id IS NOT NULL DO UPDATE SET
        contact_peer_id = EXCLUDED.contact_peer_id,
        contact_username = EXCLUDED.contact_username,
        nickname = CASE WHEN listings.manual_fields ? 'nickname' THEN listings.nickname
                        ELSE COALESCE(EXCLUDED.nickname, listings.nickname) END,
        district = CASE WHEN listings.manual_fields ? 'district' THEN listings.district
                        ELSE COALESCE(EXCLUDED.district, listings.district) END,
        bot_url = CASE WHEN listings.manual_fields ? 'bot_url' THEN listings.bot_url ELSE EXCLUDED.bot_url END,
        description = CASE WHEN listings.manual_fields ? 'description' THEN listings.description
                           ELSE EXCLUDED.description END,
        media_json = CASE WHEN listings.manual_fields ? 'media' OR EXCLUDED.media_json = '[]'::jsonb
                          THEN listings.media_json ELSE EXCLUDED.media_json END,
        evidence_json = COALESCE(listings.evidence_json, '{}'::jsonb) || EXCLUDED.evidence_json,
        last_active_at = GREATEST(listings.last_active_at, EXCLUDED.last_active_at),
        content_signature = EXCLUDED.content_signature, dedup_text = EXCLUDED.dedup_text,
        updated_at = NOW()
    RETURNING id INTO v_id;
    IF v_previous IS NULL THEN PERFORM listing_apply_lifecycle(v_id); END IF;
    RETURN v_id;
END
$fn$;

-- Release pre-existing duplicate holds that were only caused by the old
-- nickname/account heuristic. Keep active cross-account conflicts queued.
DO $migration$
DECLARE
    v_listing BIGINT;
BEGIN
    PERFORM listing_lock_intake();
    FOR v_listing IN
        SELECT id FROM listings WHERE status='duplicate_pending'
        ORDER BY source_posted_at DESC, id DESC
    LOOP
        PERFORM listing_apply_lifecycle(v_listing);
    END LOOP;
END
$migration$;
