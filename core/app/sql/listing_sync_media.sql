UPDATE listings l
SET media_json = rm.media_json, updated_at = NOW()
FROM raw_messages rm
WHERE l.id = $1 AND rm.id = $2;
