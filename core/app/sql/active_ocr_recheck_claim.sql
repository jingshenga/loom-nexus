WITH exhausted AS (
    UPDATE rule_active_ocr_rechecks
    SET status='failed',last_error='worker lease expired after maximum attempts',updated_at=NOW()
    WHERE status='processing' AND attempts >= 3
      AND updated_at < NOW()-INTERVAL '10 minutes'
    RETURNING id
), picked AS (
    SELECT q.id
    FROM rule_active_ocr_rechecks q
    JOIN raw_messages r ON r.id=q.raw_id
    WHERE q.attempts < 3
      AND q.next_attempt_at <= NOW()
      AND (q.status='waiting_ocr'
           OR (q.status='processing' AND q.updated_at < NOW()-INTERVAL '10 minutes'))
      AND r.extract_state='dropped'
      AND NOT EXISTS (
          SELECT 1 FROM listing_media_ocr o
          WHERE o.raw_id=q.raw_id AND o.status IN ('pending','processing')
      )
    ORDER BY q.id
    LIMIT GREATEST(1,$1)
    FOR UPDATE OF q SKIP LOCKED
)
UPDATE rule_active_ocr_rechecks q
SET status='processing',attempts=q.attempts+1,updated_at=NOW()
FROM picked
WHERE q.id=picked.id
RETURNING q.id,q.source_id,q.raw_id,q.raw_revision_id,q.content_hash,
          q.mode_event_id,q.rule_version_ids,q.attempts;
