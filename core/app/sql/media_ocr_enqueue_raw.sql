INSERT INTO listing_media_ocr (raw_id, listing_id, media_path)
VALUES ($1::bigint, NULL, $2::text)
ON CONFLICT (raw_id, media_path) DO NOTHING;
