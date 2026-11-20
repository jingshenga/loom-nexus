-- 存量库迁移：媒体 SHA-256 去重缓存（可重复执行）
-- 同字节图片复用已成功的 OCR 结果，跳过 RapidOCR 引擎调用
ALTER TABLE listing_media_ocr ADD COLUMN IF NOT EXISTS file_sha256 VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_listing_media_ocr_sha256
    ON listing_media_ocr(file_sha256) WHERE status = 'done';
