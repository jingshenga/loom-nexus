SELECT ingest_append_raw(
    $1::bigint, $2::bigint, $3::bigint, $4::bigint,
    $5::text, $6::jsonb, $7::timestamptz
) AS raw_id;
