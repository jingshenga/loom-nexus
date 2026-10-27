SELECT duty_snapshot_upsert(
    $1::bigint, $2::bigint, $3::date, $4::bigint, $5::varchar,
    $6::jsonb, $7::jsonb, $8::timestamptz, $9::timestamptz
) AS changed;
