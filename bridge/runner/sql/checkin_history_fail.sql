UPDATE checkin_history_scans
SET status = 'failed', error = $2::text,
    progress_note = '扫描失败，需检查来源或账号状态', updated_at = NOW()
WHERE source_id = $1::bigint;
