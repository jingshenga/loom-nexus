INSERT INTO listing_media_ocr (profile_result_id, media_path)
SELECT $1::bigint, item->>'path'
FROM jsonb_array_elements($2::jsonb) item
WHERE item->>'kind' = 'photo' AND COALESCE(item->>'path', '') <> ''
ON CONFLICT (profile_result_id, media_path) WHERE profile_result_id IS NOT NULL DO NOTHING;
