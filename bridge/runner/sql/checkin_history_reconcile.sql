UPDATE profile_collection_jobs j
SET status = 'skipped',
    error = '历史资料库已按 TG 号命中，资料卡进入库内审核/展示流程',
    stage = 'checked', progress_note = '已从持续扫描的资料库命中',
    updated_at = NOW()
WHERE j.source_id = $1::bigint
  AND j.status = 'not_found'
  AND EXISTS (
      SELECT 1 FROM listings l
      JOIN raw_messages rm ON rm.id = l.source_msg_id
      WHERE rm.source_id = j.source_id
        AND lower(l.contact_username) = lower(j.contact_username)
        AND l.status <> 'rejected'
  );
