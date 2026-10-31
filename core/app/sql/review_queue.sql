SELECT l.id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.media_json, l.evidence_json, l.confidence, l.status,
       l.contact_peer_id, l.contact_username, l.created_at,
       COALESCE(rm.raw_text, l.description) AS raw_text,
       COALESCE(s.title, profile_source.title,
                CASE WHEN l.duty_collection_job_id IS NOT NULL
                     THEN 'Telegram 个人资料/关联频道' END) AS source_title,
       COALESCE(rm.posted_at, profile_result.collected_at) AS raw_posted_at
FROM listings l
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN ingest_sources s ON s.id = rm.source_id
LEFT JOIN profile_collection_jobs profile_job
       ON profile_job.id = l.profile_collection_job_id
LEFT JOIN ingest_sources profile_source ON profile_source.id = profile_job.source_id
LEFT JOIN profile_collection_results profile_result
       ON profile_result.job_id = profile_job.id
WHERE l.status = $1::text
  AND ($4::text IS NULL OR l.nickname ILIKE '%' || $4::text || '%')
ORDER BY l.id DESC
LIMIT GREATEST(1, $2::int) OFFSET GREATEST(0, $3::int);
