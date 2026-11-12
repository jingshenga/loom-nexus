UPDATE profile_collection_results r
SET media_json = COALESCE((
    SELECT jsonb_agg(
        CASE WHEN item->>'path' = $2::text
             THEN item || jsonb_build_object(
                 'ocr_status', $3::text,
                 'ocr_classification', $4::text,
                 'ocr_review_status', $5::text,
                 'ocr_text', $6::text,
                 'ocr_confidence', $7::numeric,
                 'visibility', $8::text
             )
             ELSE item END
        ORDER BY ord
    )
    FROM jsonb_array_elements(r.media_json) WITH ORDINALITY AS a(item, ord)
), '[]'::jsonb)
WHERE r.id = $1::bigint;
