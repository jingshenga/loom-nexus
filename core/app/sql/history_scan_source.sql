SELECT EXISTS (
    SELECT 1 FROM checkin_history_scans WHERE source_id = $1::bigint
);
