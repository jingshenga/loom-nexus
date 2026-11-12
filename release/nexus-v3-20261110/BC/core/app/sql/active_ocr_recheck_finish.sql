UPDATE rule_active_ocr_rechecks
SET status=CASE
        WHEN $2='retry' AND attempts < 3 THEN 'waiting_ocr'
        WHEN $2='retry' THEN 'failed'
        ELSE $2
    END,
    last_error=LEFT(COALESCE($3,''),1000),
    next_attempt_at=CASE WHEN $2='retry' AND attempts < 3
        THEN NOW() + (INTERVAL '5 seconds' * attempts) ELSE next_attempt_at END,
    updated_at=NOW()
WHERE id=$1 AND status='processing';
