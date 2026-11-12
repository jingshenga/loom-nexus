WITH created AS (INSERT INTO listings (
    source_msg_id, duty_collection_job_id, contact_peer_id, contact_username,
    nickname, district, bot_url,
    price_amount, price_currency, description, media_json, evidence_json,
    status, confidence
)
VALUES (
    NULL, $10::bigint, $1::bigint, NULLIF($2::text, ''), NULLIF($3::text, ''),
    NULLIF($4::text, ''), NULLIF($10::text, ''), $5::numeric, COALESCE(NULLIF($6::text, ''), 'CNY'),
    NULLIF($7::text, ''), $8::jsonb, $9::jsonb, 'pending', 0.5
)
ON CONFLICT (duty_collection_job_id) WHERE duty_collection_job_id IS NOT NULL
DO UPDATE SET updated_at = NOW()
RETURNING id), marked AS (
    UPDATE duty_collection_jobs
    SET status = 'manual_card_created', updated_at = NOW()
    WHERE id = $11::bigint
    RETURNING id
)
SELECT created.id FROM created JOIN marked ON TRUE;
