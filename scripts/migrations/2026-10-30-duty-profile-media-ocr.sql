ALTER TABLE listing_media_ocr
    ALTER COLUMN raw_id DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_media_ocr_profile_media
    ON listing_media_ocr(listing_id, media_path)
    WHERE raw_id IS NULL AND listing_id IS NOT NULL;
