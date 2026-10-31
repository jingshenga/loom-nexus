SELECT DISTINCT COALESCE(rm.source_id, profile_job.source_id) AS id,
       COALESCE(s.title, profile_source.title, '来源未知') AS title
FROM listings l
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN ingest_sources s ON s.id = rm.source_id
LEFT JOIN profile_collection_jobs profile_job
       ON profile_job.id = l.profile_collection_job_id
LEFT JOIN ingest_sources profile_source ON profile_source.id = profile_job.source_id
WHERE l.status = $1::text
  AND COALESCE(rm.source_id, profile_job.source_id) IS NOT NULL
ORDER BY title;
