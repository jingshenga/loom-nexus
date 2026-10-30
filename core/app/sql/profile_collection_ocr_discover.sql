INSERT INTO listing_media_ocr (profile_result_id, media_path)
SELECT r.id, item->>'path'
FROM profile_collection_results r
CROSS JOIN LATERAL jsonb_array_elements(r.media_json) item
WHERE item->>'kind' = 'photo'
  AND COALESCE(item->>'path', '') <> ''
  AND COALESCE(item->>'ocr_status', 'pending') = 'pending'
ON CONFLICT (profile_result_id, media_path) WHERE profile_result_id IS NOT NULL DO NOTHING;
