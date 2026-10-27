UPDATE duty_collection_jobs
SET contact_peer_id = $2::bigint,
    profile_name = NULLIF($3::text, ''),
    profile_about = NULLIF($4::text, ''),
    profile_url = NULLIF($5::text, ''),
    personal_channel_id = $6::bigint,
    channel_url = NULLIF($7::text, ''),
    bot_url = NULLIF($8::text, ''),
    pinned_msg_id = $9::bigint,
    pinned_text = NULLIF($10::text, ''),
    profile_checked_at = NOW(),
    updated_at = NOW()
WHERE id = $1::bigint;
