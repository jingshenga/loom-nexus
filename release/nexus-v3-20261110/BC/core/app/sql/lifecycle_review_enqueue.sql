SELECT extract_lifecycle_review_enqueue(
    $1::bigint, NULL, $2::bigint, $3::varchar, $4::varchar, $5::varchar, $6::jsonb
) AS event_id;
