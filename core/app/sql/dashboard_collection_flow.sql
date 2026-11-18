WITH recent_messages AS (
    SELECT r.source_id,count(*) AS originals,
           count(*) FILTER (WHERE r.extract_state IN ('done','dropped')) AS processed
    FROM raw_messages r
    WHERE EXISTS (SELECT 1 FROM raw_message_revisions v WHERE v.raw_id=r.id AND v.revision=1 AND v.observed_at >= $1)
    GROUP BY r.source_id
), recent_jobs AS (
    SELECT source_id,count(*) AS assessed,
        count(*) FILTER (WHERE collection_plan_json->>'decision'='reuse'
          OR collection_plan_json#>>'{result,reason}' IN ('existing_original','existing_rule_review')) AS reused,
        count(*) FILTER (WHERE collection_plan_json->>'decision'='supplement') AS supplements,
        count(*) FILTER (WHERE collection_plan_json->>'decision'='update') AS updates,
        count(*) FILTER (WHERE collection_plan_json#>>'{result,reason}'='candidate_created') AS new_candidates,
        count(*) FILTER (WHERE status='needs_review') AS needs_review,
        count(*) FILTER (WHERE status='failed') AS failed,
        count(*) FILTER (WHERE status='not_found') AS not_found
    FROM profile_collection_jobs
    WHERE updated_at >= $1 AND collection_plan_json <> '{}'::jsonb
    GROUP BY source_id
), recent_published AS (
    SELECT COALESCE(r.source_id,j.source_id) AS source_id,count(*) AS published
    FROM listings l
    LEFT JOIN raw_messages r ON r.id=l.source_msg_id
    LEFT JOIN profile_collection_jobs j ON j.id=l.profile_collection_job_id
    WHERE l.status='published' AND l.published_at >= $1
    GROUP BY COALESCE(r.source_id,j.source_id)
)
SELECT s.id AS source_id,s.title,s.router_mode,
       COALESCE(m.originals,0) AS originals,COALESCE(m.processed,0) AS processed,
       COALESCE(j.assessed,0) AS assessed,COALESCE(j.reused,0) AS reused,
       COALESCE(j.supplements,0) AS supplements,COALESCE(j.updates,0) AS updates,
       COALESCE(j.new_candidates,0) AS new_candidates,COALESCE(j.needs_review,0) AS needs_review,
       COALESCE(j.failed,0) AS failed,COALESCE(j.not_found,0) AS not_found,
       COALESCE(p.published,0) AS published
FROM ingest_sources s
LEFT JOIN recent_messages m ON m.source_id=s.id
LEFT JOIN recent_jobs j ON j.source_id=s.id
LEFT JOIN recent_published p ON p.source_id=s.id
WHERE s.is_active ORDER BY s.id;
