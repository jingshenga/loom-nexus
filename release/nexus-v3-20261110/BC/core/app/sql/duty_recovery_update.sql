UPDATE duty_collection_jobs
SET contact_peer_id = COALESCE($2::bigint, contact_peer_id),
    profile_url = COALESCE(NULLIF($3::text, ''), profile_url),
    channel_url = COALESCE(NULLIF($4::text, ''), channel_url),
    bot_url = COALESCE(NULLIF($5::text, ''), bot_url),
    updated_at = NOW()
WHERE id = $1::bigint
RETURNING id;
