SELECT rm.raw_text FROM chat_sessions cs
LEFT JOIN listings l ON l.id = cs.listing_id
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
WHERE cs.id = $1::bigint;
