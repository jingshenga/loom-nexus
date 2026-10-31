SELECT l.id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.media_json, l.evidence_json, l.confidence, l.status,
       l.contact_peer_id, l.contact_username, l.created_at,
       COALESCE(rm.raw_text, l.description) AS raw_text,
       COALESCE(s.title, CASE WHEN ir.rule_type = 'checkin_reverse' THEN '签到来源反查 V2'
                              WHEN ir.rule_type = 'profile_collection' THEN 'TG号采集 V2'
                              WHEN l.duty_collection_job_id IS NOT NULL
                              THEN 'Telegram 个人资料/关联频道' END) AS source_title,
       rm.posted_at AS raw_posted_at
FROM listings l
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN ingest_sources s ON s.id = rm.source_id
LEFT JOIN profile_collection_jobs pcj ON pcj.id = l.profile_collection_job_id
LEFT JOIN ingest_rules ir ON ir.id = pcj.rule_id
WHERE l.status = $1::text
  AND (($8::text IS NULL AND l.profile_collection_job_id IS NULL)
       OR ($8::text IS NOT NULL AND l.evidence_json->>'review_bucket' = $8::text))
  AND ($4::text IS NULL OR l.nickname ILIKE '%' || $4::text || '%')
  AND ($5::text IS NULL OR COALESCE(s.title, CASE WHEN l.duty_collection_job_id IS NOT NULL THEN 'Telegram 个人资料/关联频道' END, '') ILIKE '%' || $5::text || '%')
  AND ($6::text IS NULL OR EXISTS (
      SELECT 1 FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m
      WHERE m->>'kind' = $6::text
  ))
ORDER BY CASE WHEN $7::text = 'oldest' THEN l.created_at END ASC,
         CASE WHEN $7::text <> 'oldest' THEN l.created_at END DESC,
         l.id DESC
LIMIT GREATEST(1, $2::int) OFFSET GREATEST(0, $3::int);
