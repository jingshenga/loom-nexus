SELECT count(*) AS total,
       count(*) FILTER (WHERE status = 'pending') AS pending,
       count(*) FILTER (WHERE status = 'processing') AS processing,
       count(*) FILTER (WHERE status = 'deferred') AS deferred,
       count(*) FILTER (WHERE status = 'fetched') AS fetched,
       count(*) FILTER (WHERE status IN ('not_found', 'stale_source', 'source_unavailable')) AS no_usable,
       count(*) FILTER (WHERE status = 'failed') AS failed,
       count(*) FILTER (
           WHERE status = 'processing'
             AND COALESCE(heartbeat_at, updated_at) < NOW() - INTERVAL '5 minutes'
       ) AS stale_processing,
       max(updated_at) AS last_updated,
       (SELECT id FROM ingest_rules
        WHERE rule_type = 'profile_collection' AND is_active
        ORDER BY id LIMIT 1) AS rule_id
FROM profile_collection_jobs;
