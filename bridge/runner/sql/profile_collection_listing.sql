SELECT id FROM listing_intake_profile($1::bigint, $2::text) AS profile(id)
WHERE id IS NOT NULL;
