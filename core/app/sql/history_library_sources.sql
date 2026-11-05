SELECT h.source_id, s.title, s.username, h.status,
       h.scanned_count, h.stored_count, h.candidate_count,
       h.cursor_msg_id, h.last_scanned_at
FROM checkin_history_scans h
JOIN ingest_sources s ON s.id = h.source_id
ORDER BY s.title, h.source_id;
