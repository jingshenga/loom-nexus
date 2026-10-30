SELECT id, raw_id, listing_id, profile_result_id, media_path
FROM listing_media_ocr
WHERE status = 'pending'
ORDER BY id
FOR UPDATE SKIP LOCKED
LIMIT 1;
