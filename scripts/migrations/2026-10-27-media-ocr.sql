BEGIN;

CREATE TABLE IF NOT EXISTS listing_media_ocr (
    id BIGSERIAL PRIMARY KEY,
    raw_id BIGINT NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
    listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
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

UPDATE raw_messages rm
SET media_json = (
    SELECT COALESCE(jsonb_agg(
        CASE WHEN item->>'kind' = 'photo'
                   AND COALESCE(item->>'ocr_status', 'pending') = 'pending'
             THEN item || jsonb_build_object('visibility', 'pending', 'ocr_status', 'pending')
             ELSE item END
        ORDER BY ord
    ), '[]'::jsonb) AS media_json
    FROM jsonb_array_elements(rm.media_json) WITH ORDINALITY AS a(item, ord)
)
WHERE EXISTS (
    SELECT 1 FROM jsonb_array_elements(rm.media_json) item
    WHERE item->>'kind' = 'photo'
);

UPDATE listings l
SET media_json = (
    SELECT COALESCE(jsonb_agg(
        CASE WHEN item->>'kind' = 'photo'
                   AND COALESCE(item->>'ocr_status', 'pending') = 'pending'
             THEN item || jsonb_build_object('visibility', 'pending', 'ocr_status', 'pending')
             ELSE item END
        ORDER BY ord
    ), '[]'::jsonb) AS media_json
    FROM jsonb_array_elements(l.media_json) WITH ORDINALITY AS a(item, ord)
)
WHERE EXISTS (
    SELECT 1 FROM jsonb_array_elements(l.media_json) item
    WHERE item->>'kind' = 'photo'
);

INSERT INTO listing_media_ocr (raw_id, listing_id, media_path)
SELECT rm.id, l.id, item->>'path'
FROM raw_messages rm
CROSS JOIN LATERAL jsonb_array_elements(rm.media_json) item
LEFT JOIN LATERAL (
    SELECT id FROM listings WHERE source_msg_id = rm.id ORDER BY id DESC LIMIT 1
) l ON TRUE
WHERE item->>'kind' = 'photo'
  AND COALESCE(item->>'path', '') <> ''
  AND COALESCE(item->>'ocr_status', 'pending') = 'pending'
ON CONFLICT (raw_id, media_path) DO NOTHING;

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
                     'visibility', p_visibility,
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

COMMIT;
