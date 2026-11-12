WITH clock AS (
    SELECT ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date AS duty_date
), reports AS (
    SELECT ds.rule_id, MAX(ds.observed_at) AS observed_at,
           COALESCE(SUM(jsonb_array_length(ds.unresolved_links)), 0)::bigint AS unresolved
    FROM duty_snapshots ds CROSS JOIN clock c
    WHERE ds.duty_date = c.duty_date
    GROUP BY ds.rule_id
), contacts AS (
    SELECT DISTINCT ds.rule_id, lower(u.username) AS username
    FROM duty_snapshots ds
    CROSS JOIN clock c
    CROSS JOIN LATERAL jsonb_array_elements_text(ds.contact_usernames) AS u(username)
    WHERE ds.duty_date = c.duty_date
), green_only AS (
    SELECT DISTINCT ds.rule_id, lower(u.username) AS username
    FROM duty_snapshots ds CROSS JOIN clock c
    CROSS JOIN LATERAL jsonb_array_elements_text(ds.green_only_usernames) AS u(username)
    WHERE ds.duty_date = c.duty_date
), green_blue AS (
    SELECT DISTINCT ds.rule_id, lower(u.username) AS username
    FROM duty_snapshots ds CROSS JOIN clock c
    CROSS JOIN LATERAL jsonb_array_elements_text(ds.green_blue_usernames) AS u(username)
    WHERE ds.duty_date = c.duty_date
)
SELECT ir.id AS rule_id,
       reports.observed_at,
       COUNT(DISTINCT c.username)::bigint AS matched,
       (SELECT COUNT(*) FROM green_blue gb WHERE gb.rule_id = ir.id)::bigint AS green_blue,
       (SELECT COUNT(*) FROM green_only go WHERE go.rule_id = ir.id)::bigint AS green_only,
       COUNT(DISTINCT c.username) FILTER (WHERE EXISTS (
           SELECT 1 FROM listings l WHERE lower(l.contact_username) = c.username
       ))::bigint AS with_card,
       COUNT(DISTINCT c.username) FILTER (WHERE EXISTS (
           SELECT 1 FROM duty_collection_jobs j CROSS JOIN clock d
           WHERE j.duty_date = d.duty_date AND lower(j.contact_username) = c.username
             AND j.status IN ('pending', 'processing')
       ))::bigint AS queued,
       COUNT(DISTINCT c.username) FILTER (WHERE EXISTS (
           SELECT 1 FROM duty_collection_jobs j CROSS JOIN clock d
           WHERE j.duty_date = d.duty_date AND lower(j.contact_username) = c.username
             AND j.status IN ('fetched', 'skipped')
       ) AND NOT EXISTS (
           SELECT 1 FROM listings l WHERE lower(l.contact_username) = c.username
       ))::bigint AS fetched,
       COUNT(DISTINCT c.username) FILTER (WHERE EXISTS (
           SELECT 1 FROM duty_collection_jobs j CROSS JOIN clock d
           WHERE j.duty_date = d.duty_date AND lower(j.contact_username) = c.username
             AND j.status IN ('failed', 'not_found')
       ))::bigint AS failed,
       COUNT(DISTINCT c.username) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM listings l WHERE lower(l.contact_username) = c.username
       ) AND NOT EXISTS (
           SELECT 1 FROM duty_collection_jobs j CROSS JOIN clock d
           WHERE j.duty_date = d.duty_date AND lower(j.contact_username) = c.username
             AND j.status <> 'cancelled'
       ))::bigint AS unqueued,
       COALESCE(reports.unresolved, 0)::bigint AS unresolved
FROM ingest_rules ir
LEFT JOIN reports ON reports.rule_id = ir.id
LEFT JOIN contacts c ON c.rule_id = ir.id
WHERE ir.rule_type = 'on_duty'
GROUP BY ir.id, reports.observed_at, reports.unresolved;
