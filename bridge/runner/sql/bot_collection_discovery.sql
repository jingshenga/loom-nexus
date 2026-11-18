INSERT INTO bot_collection_results(job_id,contact_username,profile_url,bot_url,discovery_json)
VALUES($1::bigint,$2::text,$3::text,$4::text,$5::jsonb)
ON CONFLICT(job_id) DO UPDATE SET contact_username=EXCLUDED.contact_username,
    profile_url=EXCLUDED.profile_url,bot_url=EXCLUDED.bot_url,
    discovery_json=EXCLUDED.discovery_json,collected_at=NOW()
WHERE bot_collection_results.verification_status='unverified';
