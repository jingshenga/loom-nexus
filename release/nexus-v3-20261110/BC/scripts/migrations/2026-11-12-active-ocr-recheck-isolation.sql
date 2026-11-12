-- Correct the original OCR recheck trigger: only refresh an open Active review
-- candidate from this durable queue; never return raw_messages to Legacy claims.
BEGIN;

DROP TRIGGER IF EXISTS trg_active_raw_after_ocr ON listing_media_ocr;
DROP FUNCTION IF EXISTS requeue_active_raw_after_ocr();

ALTER TABLE raw_messages
    ADD COLUMN IF NOT EXISTS ocr_review_pending BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE raw_messages SET ocr_review_pending=FALSE WHERE ocr_review_pending;

CREATE TABLE IF NOT EXISTS rule_active_ocr_rechecks (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    raw_id BIGINT NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
    raw_revision_id BIGINT NOT NULL REFERENCES raw_message_revisions(id) ON DELETE CASCADE,
    content_hash VARCHAR(64) NOT NULL,
    mode_event_id BIGINT NOT NULL DEFAULT 0,
    rule_version_ids BIGINT[] NOT NULL DEFAULT '{}',
    status VARCHAR(16) NOT NULL DEFAULT 'waiting_ocr'
        CHECK (status IN ('waiting_ocr','processing','done','failed','stale')),
    attempts SMALLINT NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_id,raw_id,raw_revision_id)
);

CREATE INDEX IF NOT EXISTS idx_rule_active_ocr_rechecks_ready
    ON rule_active_ocr_rechecks(next_attempt_at,id)
    WHERE status IN ('waiting_ocr','processing');

-- The admin OCR retry path may recover a failed image. Reopen only its
-- matching, still-open Active review task; this trigger never touches listings.
CREATE OR REPLACE FUNCTION reopen_active_ocr_review_after_retry()
RETURNS TRIGGER
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.raw_id IS NULL OR OLD.status NOT IN ('failed','done') OR NEW.status <> 'pending' THEN
        RETURN NEW;
    END IF;
    UPDATE rule_active_ocr_rechecks task
    SET status='waiting_ocr',attempts=0,next_attempt_at=NOW(),last_error='',updated_at=NOW()
    FROM raw_messages raw
    JOIN ingest_sources src ON src.id=raw.source_id AND src.router_mode='active'
    JOIN LATERAL (
        SELECT id,content_hash FROM raw_message_revisions
        WHERE raw_id=raw.id ORDER BY revision DESC LIMIT 1
    ) rev ON TRUE
    WHERE task.raw_id=NEW.raw_id
      AND task.raw_revision_id=rev.id
      AND task.content_hash=rev.content_hash
      AND task.status IN ('failed','done')
      AND EXISTS (
          SELECT 1 FROM rule_active_review_queue review
          WHERE review.source_id=raw.source_id AND review.raw_id=raw.id
            AND review.content_hash=rev.content_hash AND review.status='open'
      )
      AND task.mode_event_id=COALESCE((
          SELECT MAX(event.id) FROM rule_router_mode_events event WHERE event.source_id=raw.source_id
      ),0)
      AND raw.id=task.raw_id;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_active_ocr_review_retry ON listing_media_ocr;
CREATE TRIGGER trg_active_ocr_review_retry
AFTER UPDATE OF status ON listing_media_ocr
FOR EACH ROW EXECUTE FUNCTION reopen_active_ocr_review_after_retry();

COMMIT;
