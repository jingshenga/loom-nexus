BEGIN;

ALTER TABLE listings ADD COLUMN IF NOT EXISTS manual_fields JSONB NOT NULL DEFAULT '{}';

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
    UPDATE listings SET media_json = v_media, updated_at = NOW()
    WHERE source_msg_id = p_raw_id;
    RETURN TRUE;
END
$fn$;

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

    -- Newest-wins: archive prior published cards of the same contact.
    UPDATE listings l
    SET status = 'archived', updated_at = NOW()
    WHERE l.status = 'published'
      AND ((p_contact_username IS NOT NULL AND lower(l.contact_username) = lower(p_contact_username))
        OR (p_contact_peer_id IS NOT NULL AND l.contact_peer_id = p_contact_peer_id AND l.contact_peer_id <> 0));
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
    ) RETURNING id INTO v_id;
    RETURN QUERY SELECT v_id, v_status, v_conflicted;
END
$fn$;

COMMIT;
