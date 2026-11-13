-- Keep this migration safe for installations that applied the early draft:
-- OCR completion must never put a processed raw message back into the Legacy
-- extraction queue. Re-evaluation is handled by the isolated review queue.
BEGIN;

ALTER TABLE raw_messages
    ADD COLUMN IF NOT EXISTS ocr_review_pending BOOLEAN NOT NULL DEFAULT FALSE;

DROP TRIGGER IF EXISTS trg_active_raw_after_ocr ON listing_media_ocr;
DROP FUNCTION IF EXISTS requeue_active_raw_after_ocr();

COMMIT;
