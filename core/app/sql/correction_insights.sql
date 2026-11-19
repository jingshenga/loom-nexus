SELECT field_name, old_value, new_value, count(*) AS hits
FROM listing_correction_diffs
WHERE created_at >= now() - make_interval(days => $1::int)
GROUP BY field_name, old_value, new_value
HAVING count(*) >= $2::int
ORDER BY hits DESC, field_name, old_value
LIMIT 50;
