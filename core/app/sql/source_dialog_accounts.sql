SELECT account_id
FROM dialog_cache
WHERE peer_id = $1::bigint
  AND account_id = ANY($2::bigint[]);
