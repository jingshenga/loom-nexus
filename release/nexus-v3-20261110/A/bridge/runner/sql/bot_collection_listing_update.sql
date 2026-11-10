UPDATE listings
SET bot_url = CASE WHEN manual_fields ? 'bot_url' THEN bot_url ELSE $2 END,
    updated_at = NOW()
WHERE lower(contact_username) = lower($1);
