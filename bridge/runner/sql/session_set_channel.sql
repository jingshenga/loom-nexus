UPDATE chat_sessions SET delivery_channel = $2::varchar,
       fallback_bot = COALESCE($3::varchar, fallback_bot)
WHERE id = $1::bigint;
