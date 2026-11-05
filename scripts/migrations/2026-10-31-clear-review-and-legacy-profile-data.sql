-- Run once only after a verified database backup.
-- Preserve every media file under the media volume; this only removes DB rows.
BEGIN;

-- Clear the pending review workbench, including duplicate candidates.
DELETE FROM listings WHERE status IN ('pending', 'duplicate_pending');

-- Remove old TG/profile V2 results and their jobs. Published listings survive;
-- the FK added by 2026-10-31-checkin-reverse-v2.sql is ON DELETE SET NULL.
DELETE FROM profile_collection_jobs j
USING ingest_rules r
WHERE j.rule_id = r.id AND r.rule_name = 'TG号采集V2';

-- OCR annotations and links are disposable; the referenced image files remain.
DELETE FROM listing_media_ocr;

COMMIT;
