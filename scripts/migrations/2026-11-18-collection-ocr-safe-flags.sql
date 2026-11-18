BEGIN;

-- Keep the existing manual OCR operation separate from unattended flags.
CREATE OR REPLACE FUNCTION nexus_media_ocr_automatic_flags(
    p_raw_id BIGINT, p_media_path TEXT, p_visibility TEXT, p_status TEXT, p_classification TEXT
) RETURNS BOOLEAN LANGUAGE plpgsql AS $fn$
DECLARE
    flags JSONB := jsonb_build_object('visibility',p_visibility,'ocr_status',p_status,
                                      'ocr_classification',p_classification);
BEGIN
    UPDATE raw_messages r SET media_json=COALESCE((
        SELECT jsonb_agg(CASE WHEN item->>'path'=p_media_path
            THEN item || CASE WHEN item->'visibility_override'='true'::jsonb THEN flags-'visibility' ELSE flags END
            ELSE item END ORDER BY ord)
        FROM jsonb_array_elements(r.media_json) WITH ORDINALITY AS m(item,ord)
    ),'[]') WHERE id=p_raw_id;
    IF NOT FOUND THEN RETURN FALSE; END IF;

    UPDATE listings l SET media_json=COALESCE((
        SELECT jsonb_agg(CASE WHEN item->>'path'=p_media_path
            THEN item || CASE WHEN item->'visibility_override'='true'::jsonb THEN flags-'visibility' ELSE flags END
            ELSE item END ORDER BY ord)
        FROM jsonb_array_elements(l.media_json) WITH ORDINALITY AS m(item,ord)
    ),'[]'),updated_at=NOW()
    WHERE source_msg_id=p_raw_id AND status IN ('pending','duplicate_pending')
      AND NOT (manual_fields ? 'media')
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(l.media_json) m WHERE m->>'path'=p_media_path);
    RETURN TRUE;
END;
$fn$;

COMMIT;
