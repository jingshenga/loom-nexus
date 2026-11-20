SELECT fwd.from_id,
       MAX(fwd.from_name) AS from_name,
       MIN(fwd.from_type) AS from_type,
       count(*) AS forward_count,
       count(DISTINCT rm.source_id) AS source_count,
       MAX(rm.posted_at) AS latest_forwarded_at,
       bool_or(s.id IS NOT NULL) AS is_already_source,
       MAX(s.title) AS known_title
FROM raw_messages rm
CROSS JOIN LATERAL (
    SELECT rm.fwd_from_json->>'from_id' AS from_id,
           rm.fwd_from_json->>'from_name' AS from_name,
           rm.fwd_from_json->>'from_type' AS from_type
) fwd
LEFT JOIN ingest_sources s
    ON s.peer_id::text = fwd.from_id
    OR s.peer_id::text = '-100' || fwd.from_id
WHERE rm.fwd_from_json IS NOT NULL
GROUP BY fwd.from_id
ORDER BY count(*) DESC, MAX(rm.posted_at) DESC NULLS LAST
LIMIT $1::int;
