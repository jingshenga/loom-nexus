SELECT count(*)
FROM listings l
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN profile_collection_jobs profile_job
       ON profile_job.id = l.profile_collection_job_id
LEFT JOIN profile_collection_results profile_result
       ON profile_result.job_id = profile_job.id
WHERE l.status = $1::text
  AND ($2::text IS NULL OR l.nickname ILIKE '%' || $2::text || '%')
  AND ($3::bigint IS NULL OR COALESCE(rm.source_id, profile_job.source_id) = $3::bigint)
  AND ($4::text = 'all'
       OR ($4::text = 'with_media' AND jsonb_array_length(COALESCE(l.media_json, '[]'::jsonb)) > 0)
       OR ($4::text = 'no_media' AND jsonb_array_length(COALESCE(l.media_json, '[]'::jsonb)) = 0))
  AND (NOT $5::boolean OR EXISTS (
       SELECT 1 FROM listing_media_ocr o
       WHERE o.review_status = 'pending'
         AND (o.listing_id = l.id
              OR o.raw_id = l.source_msg_id
              OR o.profile_result_id = profile_result.id)
  ));
