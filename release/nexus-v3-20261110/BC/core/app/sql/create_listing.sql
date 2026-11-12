SELECT extract_create_listing(
    $1::bigint, $2::bigint, $3::text, $4::text, $5::text,
    $6::numeric, $7::text, $8::text, $9::jsonb, $10::jsonb, $11::numeric
) AS listing_id;
