SELECT h.source_id, h.source_username, h.status, h.cursor_msg_id,
       h.scanned_count, h.stored_count, h.candidate_count,
       h.last_scanned_msg_id, h.last_scanned_at, h.next_attempt_at,
       h.started_at, h.completed_at, h.error, h.progress_note,
       (SELECT count(*) FROM listings l JOIN raw_messages rm ON rm.id = l.source_msg_id
        WHERE rm.source_id = h.source_id AND l.status = 'published') AS published_count,
       (SELECT count(*) FROM listings l JOIN raw_messages rm ON rm.id = l.source_msg_id
        WHERE rm.source_id = h.source_id AND l.status IN ('pending','duplicate_pending')) AS review_count
FROM checkin_history_scans h
JOIN source_rule_bindings b ON b.source_id = h.source_id AND b.rule_id = $1::bigint
LIMIT 1;
