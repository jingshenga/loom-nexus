SELECT ir.id AS rule_id,
       MAX(ds.observed_at) AS observed_at,
       COALESCE(SUM(jsonb_array_length(ds.contact_usernames)), 0)::bigint AS matched,
       COALESCE(SUM(jsonb_array_length(ds.unresolved_links)), 0)::bigint AS unresolved
FROM ingest_rules ir
LEFT JOIN duty_snapshots ds ON ds.rule_id = ir.id
    AND ds.duty_date = ((NOW() AT TIME ZONE 'Asia/Shanghai') - INTERVAL '4 hours')::date
WHERE ir.rule_type = 'on_duty'
GROUP BY ir.id;
