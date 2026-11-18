SELECT l.id, l.nickname, l.district, l.price_amount, l.price_currency,
       l.description, l.custom_fields, l.media_json, l.evidence_json,
       l.confidence, l.status, l.contact_peer_id, l.contact_username,
       l.bot_url, l.created_at, COALESCE(rm.raw_text, l.description) AS raw_text,
       COALESCE(s.title, CASE WHEN ir.rule_type = 'checkin_reverse' THEN '签到来源反查 V2'
                              WHEN ir.rule_type = 'bot_collection' THEN 'Bot地址采集'
                              WHEN ir.rule_type = 'profile_collection' THEN 'TG号采集 V2'
                              WHEN l.duty_collection_job_id IS NOT NULL
                              THEN 'Telegram 个人资料/关联频道' END) AS source_title,
       rm.posted_at AS raw_posted_at, l.source_msg_id, l.profile_collection_job_id
FROM listings l
LEFT JOIN raw_messages rm ON rm.id = l.source_msg_id
LEFT JOIN ingest_sources s ON s.id = rm.source_id
LEFT JOIN profile_collection_jobs pcj ON pcj.id = l.profile_collection_job_id
LEFT JOIN ingest_rules ir ON ir.id = pcj.rule_id
WHERE l.id = $1::bigint;
