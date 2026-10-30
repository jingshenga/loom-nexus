INSERT INTO profile_collection_results (
    job_id, contact_peer_id, contact_username, profile_url, channel_url,
    bot_url, description, media_json, evidence_json, profile_config
) VALUES (
    $1::bigint, $2::bigint, $3::text, $4::text, $5::text,
    $6::text, $7::text, $8::jsonb, $9::jsonb, $10::jsonb
)
ON CONFLICT (job_id) DO UPDATE SET
    contact_peer_id = EXCLUDED.contact_peer_id,
    contact_username = EXCLUDED.contact_username,
    profile_url = EXCLUDED.profile_url,
    channel_url = EXCLUDED.channel_url,
    bot_url = EXCLUDED.bot_url,
    description = EXCLUDED.description,
    media_json = EXCLUDED.media_json,
    evidence_json = EXCLUDED.evidence_json,
    profile_config = EXCLUDED.profile_config,
    collected_at = NOW()
RETURNING id;
