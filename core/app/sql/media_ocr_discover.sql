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

INSERT INTO listing_media_ocr (raw_id, listing_id, media_path)
SELECT NULL, l.id,
       CASE WHEN item->>'kind' = 'video' THEN item->>'poster' ELSE item->>'path' END
FROM listings l
CROSS JOIN LATERAL jsonb_array_elements(l.media_json) item
WHERE l.duty_collection_job_id IS NOT NULL
  AND (
      (item->>'kind' = 'photo' AND COALESCE(item->>'path', '') <> '')
      OR (item->>'kind' = 'video' AND COALESCE(item->>'poster', '') <> '')
  )
ON CONFLICT DO NOTHING;
