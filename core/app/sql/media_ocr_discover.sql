INSERT INTO listing_media_ocr (raw_id, listing_id, media_path)
SELECT rm.id, l.id, item->>'path'
FROM raw_messages rm
CROSS JOIN LATERAL jsonb_array_elements(rm.media_json) item
LEFT JOIN LATERAL (
    SELECT id FROM listings WHERE source_msg_id = rm.id ORDER BY id DESC LIMIT 1
) l ON TRUE
WHERE item->>'kind' = 'photo'
  AND COALESCE(item->>'ocr_status', 'pending') = 'pending'
  AND COALESCE(item->>'path', '') <> ''
ON CONFLICT (raw_id, media_path) DO NOTHING;
