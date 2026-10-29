UPDATE listing_media_ocr
SET status = 'pending', updated_at = NOW()
WHERE status = 'processing';
