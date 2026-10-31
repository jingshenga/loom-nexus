SELECT l.id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.media_json, l.evidence_json, l.confidence, l.status,
       l.contact_peer_id, l.contact_username, l.created_at,
       COALESCE(rm.raw_text, l.description) AS raw_text,
       COALESCE(s.title, profile_source.title,
                CASE WHEN l.duty_collection_job_id IS NOT NULL
                     THEN 'Telegram 个人资料/关联频道' END) AS source_title,
       COALESCE(rm.posted_at, profile_result.collected_at) AS raw_posted_at,
       COALESCE(rm.source_id, profile_job.source_id) AS source_id,
       jsonb_array_length(COALESCE(l.media_json, '[]'::jsonb)) > 0 AS has_media,
       EXISTS (
           SELECT 1 FROM listing_media_ocr o
           WHERE o.review_status = 'pending'
             AND (o.listing_id = l.id
                  OR o.raw_id = l.source_msg_id
                  OR o.profile_result_id = profile_result.id)
       ) AS ocr_pending
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
  AND ($5::bigint IS NULL OR COALESCE(rm.source_id, profile_job.source_id) = $5::bigint)
  AND ($6::text = 'all'
       OR ($6::text = 'with_media' AND jsonb_array_length(COALESCE(l.media_json, '[]'::jsonb)) > 0)
       OR ($6::text = 'no_media' AND jsonb_array_length(COALESCE(l.media_json, '[]'::jsonb)) = 0))
  AND (NOT $7::boolean OR EXISTS (
       SELECT 1 FROM listing_media_ocr o
       WHERE o.review_status = 'pending'
         AND (o.listing_id = l.id
              OR o.raw_id = l.source_msg_id
              OR o.profile_result_id = profile_result.id)
  ))
ORDER BY CASE WHEN $8::text = 'oldest' THEN l.created_at END ASC,
         CASE WHEN $8::text = 'newest' THEN l.created_at END DESC,
         CASE WHEN $8::text = 'oldest' THEN l.id END ASC,
         CASE WHEN $8::text = 'newest' THEN l.id END DESC
LIMIT GREATEST(1, $2::int) OFFSET GREATEST(0, $3::int);
