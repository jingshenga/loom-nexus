WITH desired AS (
    SELECT item->>'path' AS media_path
    FROM jsonb_array_elements($2::jsonb) item
    WHERE item->>'kind' = 'photo' AND COALESCE(item->>'path', '') <> ''
), removed AS (
    DELETE FROM listing_media_ocr o
    WHERE o.profile_result_id = $1::bigint
      AND NOT EXISTS (
          SELECT 1 FROM desired d WHERE d.media_path = o.media_path
      )
), enqueued AS (
    INSERT INTO listing_media_ocr (profile_result_id, listing_id, media_path)
    SELECT $1::bigint, l.id, d.media_path
    FROM desired d
    JOIN profile_collection_results r ON r.id = $1::bigint
    LEFT JOIN listings l ON l.profile_collection_job_id = r.job_id
    ON CONFLICT (profile_result_id, media_path) WHERE profile_result_id IS NOT NULL
    DO UPDATE SET listing_id = COALESCE(listing_media_ocr.listing_id, EXCLUDED.listing_id)
    RETURNING id
)
SELECT count(*) FROM enqueued;
