SELECT id, raw_text, posted_at
FROM raw_messages
WHERE source_id = $1::bigint
  AND length(COALESCE(raw_text, '')) >= 24
ORDER BY id DESC
LIMIT $2::int;
