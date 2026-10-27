WITH inserted AS (
    INSERT INTO listings (
        source_msg_id, duty_collection_job_id, contact_peer_id,
        contact_username, nickname, description, media_json,
        evidence_json, status, confidence
    ) VALUES (
        NULL, $1::bigint, $2::bigint, $3::text, NULLIF($4::text, ''),
        NULLIF($5::text, ''), $6::jsonb, $7::jsonb, 'pending', 0.5
    )
    ON CONFLICT DO NOTHING
    RETURNING id
)
SELECT id FROM inserted
UNION ALL
SELECT id FROM listings WHERE duty_collection_job_id = $1::bigint
LIMIT 1;
