SELECT * FROM rule_upsert_versioned(
    $1::varchar, $2::varchar, $3::jsonb, $4::boolean, $5::varchar,
    $6::varchar, $7::bigint, $8::int, $9::varchar, $10::varchar
);
