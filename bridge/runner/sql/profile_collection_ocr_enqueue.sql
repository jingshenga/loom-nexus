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
    INSERT INTO listing_media_ocr (profile_result_id, media_path)
    SELECT $1::bigint, media_path FROM desired
    ON CONFLICT (profile_result_id, media_path) WHERE profile_result_id IS NOT NULL DO NOTHING
    RETURNING id
)
SELECT count(*) FROM enqueued;
