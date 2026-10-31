BEGIN;

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

COMMIT;
