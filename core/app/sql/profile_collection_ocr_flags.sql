WITH updated_result AS (
    UPDATE profile_collection_results r
    SET media_json = COALESCE((
        SELECT jsonb_agg(
            CASE WHEN item->>'path' = $2::text
                 THEN item || jsonb_build_object(
                     'visibility', CASE
                         WHEN item->'visibility_override' = 'true'::jsonb
                             THEN item->>'visibility'
                         WHEN $4::text IN ('info_card', 'uncertain') THEN 'admin'
                         ELSE 'public'
                     END,
                     'ocr_status', $3::text,
                     'ocr_classification', $4::text,
                     'ocr_review_status', $5::text,
                     'ocr_text', $6::text,
                     'ocr_confidence', $7::numeric
                 )
                 ELSE item END
            ORDER BY ord
        )
        FROM jsonb_array_elements(r.media_json) WITH ORDINALITY AS a(item, ord)
    ), '[]'::jsonb)
    WHERE r.id = $1::bigint
    RETURNING r.job_id
)
UPDATE listings l
SET media_json = COALESCE((
    SELECT jsonb_agg(
        CASE WHEN item->>'path' = $2::text
             THEN item || jsonb_build_object(
                 'visibility', CASE
                     WHEN item->'visibility_override' = 'true'::jsonb
                         THEN item->>'visibility'
                     WHEN $4::text IN ('info_card', 'uncertain') THEN 'admin'
                     ELSE 'public'
                 END,
                 'ocr_status', $3::text,
                 'ocr_classification', $4::text,
                 'ocr_review_status', $5::text,
                 'ocr_text', $6::text,
                 'ocr_confidence', $7::numeric
             )
             ELSE item END
        ORDER BY ord
    )
    FROM jsonb_array_elements(l.media_json) WITH ORDINALITY AS a(item, ord)
), '[]'::jsonb), updated_at = NOW()
FROM updated_result r
WHERE l.profile_collection_job_id = r.job_id;
