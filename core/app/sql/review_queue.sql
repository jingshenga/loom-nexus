SELECT l.id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.media_json, l.evidence_json, l.custom_fields, l.confidence, l.status,
       l.contact_peer_id, l.contact_username, l.bot_url, l.created_at,
       COALESCE(rm.raw_text, l.description) AS raw_text,
       COALESCE(s.title, CASE WHEN ir.rule_type = 'checkin_reverse' THEN '签到来源反查 V2'
                              WHEN ir.rule_type = 'bot_collection' THEN 'Bot地址采集'
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
  AND ($8::text IS NULL OR l.evidence_json->>'review_bucket' = $8::text)
  AND ($4::text IS NULL OR l.nickname ILIKE '%' || $4::text || '%')
  AND ($5::text IS NULL OR COALESCE(s.title, CASE WHEN l.duty_collection_job_id IS NOT NULL THEN 'Telegram 个人资料/关联频道' END, '') ILIKE '%' || $5::text || '%')
  AND ($6::text IS NULL OR EXISTS (
      SELECT 1 FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m
      WHERE m->>'kind' = $6::text
  ))
  AND ($9::date IS NULL OR (l.created_at AT TIME ZONE 'Asia/Shanghai')::date >= $9::date)
  AND ($10::date IS NULL OR (l.created_at AT TIME ZONE 'Asia/Shanghai')::date <= $10::date)
  AND ($11::int IS NULL OR (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m
       WHERE m->>'kind' IN ('photo','video') AND COALESCE(m->>'path',m->>'poster','') <> '') >= $11::int)
  AND ($12::int IS NULL OR (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m
       WHERE m->>'kind' IN ('photo','video') AND COALESCE(m->>'path',m->>'poster','') <> '') <= $12::int)
  AND ($13::date IS NULL OR (rm.posted_at AT TIME ZONE 'Asia/Shanghai')::date >= $13::date)
  AND ($14::date IS NULL OR (rm.posted_at AT TIME ZONE 'Asia/Shanghai')::date <= $14::date)
  AND ($1::text <> 'pending' OR NOT EXISTS (
      SELECT 1 FROM listings other
      WHERE other.id <> l.id
        AND other.status IN ('pending', 'duplicate_pending', 'published', 'archived')
        AND NULLIF(l.nickname, '') IS NOT NULL
        AND NULLIF(other.nickname, '') IS NOT NULL
        AND ((l.contact_peer_id IS NOT NULL AND l.contact_peer_id = other.contact_peer_id)
             OR (NULLIF(l.contact_username, '') IS NOT NULL
                 AND lower(l.contact_username) = lower(other.contact_username)
                 AND (l.contact_peer_id IS NULL OR other.contact_peer_id IS NULL
                      OR l.contact_peer_id = other.contact_peer_id)))
        AND (l.profile_collection_job_id IS NOT NULL OR other.profile_collection_job_id IS NOT NULL
             OR EXISTS (SELECT 1 FROM raw_messages history_raw
                        JOIN checkin_history_scans hs ON hs.source_id=history_raw.source_id
                        WHERE history_raw.id=l.source_msg_id OR history_raw.id=other.source_msg_id))
        AND NOT EXISTS (
            SELECT 1 FROM listing_duplicate_decisions d
            WHERE d.left_listing_id = LEAST(l.id, other.id)
              AND d.right_listing_id = GREATEST(l.id, other.id)
        )
  ))
ORDER BY CASE WHEN $15::text = 'original' AND $7::text = 'oldest' THEN rm.posted_at END ASC NULLS LAST,
         CASE WHEN $15::text = 'original' AND $7::text <> 'oldest' THEN rm.posted_at END DESC NULLS LAST,
         CASE WHEN $15::text = 'collected' AND $7::text = 'oldest' THEN l.created_at END ASC,
         CASE WHEN $15::text = 'collected' AND $7::text <> 'oldest' THEN l.created_at END DESC,
         CASE WHEN $15::text = 'media' AND $7::text = 'oldest' THEN (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m WHERE m->>'kind' IN ('photo','video') AND COALESCE(m->>'path',m->>'poster','') <> '') END ASC,
         CASE WHEN $15::text = 'media' AND $7::text <> 'oldest' THEN (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m WHERE m->>'kind' IN ('photo','video') AND COALESCE(m->>'path',m->>'poster','') <> '') END DESC,
         CASE WHEN $16::text = 'original' AND $7::text = 'oldest' THEN rm.posted_at END ASC NULLS LAST,
         CASE WHEN $16::text = 'original' AND $7::text <> 'oldest' THEN rm.posted_at END DESC NULLS LAST,
         CASE WHEN $16::text = 'collected' AND $7::text = 'oldest' THEN l.created_at END ASC,
         CASE WHEN $16::text = 'collected' AND $7::text <> 'oldest' THEN l.created_at END DESC,
         CASE WHEN $16::text = 'media' AND $7::text = 'oldest' THEN (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m WHERE m->>'kind' IN ('photo','video') AND COALESCE(m->>'path',m->>'poster','') <> '') END ASC,
         CASE WHEN $16::text = 'media' AND $7::text <> 'oldest' THEN (SELECT count(*) FROM jsonb_array_elements(COALESCE(l.media_json, '[]'::jsonb)) m WHERE m->>'kind' IN ('photo','video') AND COALESCE(m->>'path',m->>'poster','') <> '') END DESC,
         l.id DESC
LIMIT GREATEST(1, $2::int) OFFSET GREATEST(0, $3::int);
