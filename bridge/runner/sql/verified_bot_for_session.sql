SELECT b.bot_url
FROM chat_sessions cs
JOIN bot_collection_results b
  ON lower(b.contact_username) = lower(cs.contact_username)
WHERE cs.id = $1::bigint
  AND b.verification_status = 'verified'
  AND NULLIF(BTRIM(b.bot_url), '') IS NOT NULL
ORDER BY b.collected_at DESC, b.job_id DESC;
