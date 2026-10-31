SELECT a.id AS account_id, a.display_name, a.username, a.status,
       CASE WHEN a.duty_profile_lookup_after > NOW()
            THEN a.duty_profile_lookup_after END AS cooldown_until,
       (SELECT count(*) FROM profile_collection_jobs j
        WHERE j.account_id = a.id AND j.status = 'processing') AS processing_jobs,
       (SELECT count(*) FROM profile_collection_jobs j
        WHERE j.account_id = a.id AND j.status = 'fetched'
          AND j.updated_at >= NOW() - INTERVAL '24 hours') AS fetched_24h,
       (SELECT max(j.updated_at) FROM profile_collection_jobs j
        WHERE j.account_id = a.id AND j.status IN ('fetched', 'not_found', 'failed')) AS last_finished_at,
       (SELECT count(*) FROM ingest_sources s
        WHERE s.is_active AND s.assigned_account_id = a.id
           OR s.is_active AND EXISTS (
               SELECT 1 FROM ingest_source_account_backups b
               WHERE b.source_id = s.id AND b.account_id = a.id
           )) AS eligible_sources
FROM bridge_accounts a
ORDER BY a.id;
