SELECT l.id AS listing_id, rm.id AS raw_id
FROM listings l
JOIN raw_messages rm ON rm.id = l.source_msg_id
JOIN ingest_sources s ON s.id = rm.source_id
JOIN bridge_accounts a ON a.id = s.assigned_account_id AND a.status = 'active'
WHERE l.id = ANY($1::bigint[])
  AND s.is_active
  AND s.assigned_account_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM listing_recollect_jobs j
      WHERE j.listing_id = l.id AND j.status IN ('pending', 'processing')
  )
ORDER BY l.id;
