SELECT count(*) AS total,
       count(*) FILTER (WHERE status = 'pending') AS pending,
       count(*) FILTER (WHERE status = 'processing') AS processing,
       count(*) FILTER (WHERE status = 'deferred') AS deferred,
       count(*) FILTER (WHERE status = 'fetched') AS fetched,
       count(*) FILTER (WHERE status IN ('skipped', 'stale_source', 'source_unavailable', 'cancelled')) AS skipped,
       count(*) FILTER (WHERE status = 'not_found') AS not_found,
       count(*) FILTER (WHERE status = 'failed') AS failed,
       count(*) FILTER (WHERE status IN ('fetched', 'skipped', 'not_found', 'failed', 'stale_source', 'source_unavailable', 'cancelled')) AS completed,
       (
           SELECT max(a.duty_profile_lookup_after)
           FROM bridge_accounts a
           WHERE a.duty_profile_lookup_after > NOW()
             AND EXISTS (
                 SELECT 1 FROM profile_collection_jobs j
                 WHERE j.rule_id = $1::bigint
                   AND j.account_id = a.id
                   AND j.status IN ('pending', 'processing', 'deferred')
             )
       ) AS cooldown_until
FROM profile_collection_jobs
WHERE rule_id = $1::bigint;
