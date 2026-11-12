-- Durable, one-shot re-evaluation after OCR completes for an Active review candidate.
BEGIN;

ALTER TABLE raw_messages
    ADD COLUMN IF NOT EXISTS ocr_review_pending BOOLEAN NOT NULL DEFAULT FALSE;

CREATE OR REPLACE FUNCTION requeue_active_raw_after_ocr()
RETURNS TRIGGER
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.raw_id IS NOT NULL AND NEW.status IN ('done','failed')
       AND OLD.status IS DISTINCT FROM NEW.status THEN
        UPDATE raw_messages
        SET extract_state = CASE WHEN EXISTS (
                SELECT 1 FROM listing_media_ocr
                WHERE raw_id = NEW.raw_id AND status = 'done'
            ) THEN 'new' ELSE extract_state END,
            ocr_review_pending = FALSE
        WHERE id = NEW.raw_id
          AND ocr_review_pending
          AND extract_state = 'dropped'
          AND NOT EXISTS (
              SELECT 1 FROM listing_media_ocr
              WHERE raw_id = NEW.raw_id AND status IN ('pending','processing')
          );
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_active_raw_after_ocr ON listing_media_ocr;
CREATE TRIGGER trg_active_raw_after_ocr
AFTER UPDATE OF status ON listing_media_ocr
FOR EACH ROW EXECUTE FUNCTION requeue_active_raw_after_ocr();

COMMIT;
