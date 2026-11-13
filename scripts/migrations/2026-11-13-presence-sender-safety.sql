BEGIN;

ALTER TABLE bridge_accounts
    ADD COLUMN IF NOT EXISTS flood_wait_until TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION account_flood_wait_set(
    p_account_id BIGINT,
    p_seconds INTEGER
) RETURNS TIMESTAMPTZ
LANGUAGE plpgsql AS $fn$
DECLARE v_until TIMESTAMPTZ;
BEGIN
    UPDATE bridge_accounts
    SET flood_wait_until = GREATEST(
        COALESCE(flood_wait_until, NOW()),
        NOW() + make_interval(secs => GREATEST(p_seconds, 1))
    )
    WHERE id = p_account_id
    RETURNING flood_wait_until INTO v_until;
    RETURN v_until;
END
$fn$;

CREATE OR REPLACE FUNCTION presence_bind_peer(
    p_username VARCHAR(128),
    p_peer_id BIGINT
) RETURNS INTEGER
LANGUAGE plpgsql AS $fn$
DECLARE
    v_count INTEGER := 0;
BEGIN
    UPDATE listings
    SET contact_peer_id = p_peer_id
    WHERE lower(contact_username) = lower(p_username)
      AND contact_peer_id IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$fn$;

CREATE OR REPLACE FUNCTION outbox_claim_job(p_account_id BIGINT)
RETURNS TABLE(job_id BIGINT, chat_msg_id BIGINT, session_id BIGINT,
              contact_peer_id BIGINT, contact_username VARCHAR(128), client_msg_id VARCHAR(64),
              content TEXT, display_name VARCHAR(128), is_first BOOLEAN, has_inbound BOOLEAN,
              limit_mode VARCHAR(16), delivery_channel VARCHAR(16), fallback_bot VARCHAR(128),
              media_kind VARCHAR(16), media_b64 TEXT)
LANGUAGE plpgsql AS $fn$
DECLARE v_job BIGINT;
BEGIN
    WITH due AS (
        SELECT oj.id
        FROM outbox_jobs oj
        JOIN chat_messages cm ON cm.id = oj.chat_msg_id
        JOIN chat_sessions cs ON cs.id = cm.session_id
        JOIN bridge_accounts ba ON ba.id = p_account_id
          AND ba.status = 'active'
          AND (ba.flood_wait_until IS NULL OR ba.flood_wait_until <= NOW())
        WHERE cs.assigned_account_id = p_account_id
          AND cs.status = 'open'
          AND ((oj.status = 'pending' AND oj.run_after <= NOW())
            OR (oj.status = 'processing' AND oj.run_after < NOW() - INTERVAL '10 minutes'))
        ORDER BY oj.id
        LIMIT 1
        FOR UPDATE OF oj, cs, ba SKIP LOCKED
    )
    UPDATE outbox_jobs o
    SET status = 'processing', run_after = NOW()
    FROM due
    WHERE o.id = due.id
    RETURNING o.id INTO v_job;
    IF v_job IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT oj.id, oj.chat_msg_id, cs.id, cs.contact_peer_id, cs.contact_username,
           cm.client_msg_id, cm.content, cm.outbound_alias_snapshot,
           NOT EXISTS (SELECT 1 FROM chat_messages m2
                       WHERE m2.session_id = cs.id AND m2.direction = 'out'
                         AND m2.id <> cm.id),
           EXISTS (SELECT 1 FROM chat_messages m3
                   WHERE m3.session_id = cs.id AND m3.direction = 'in'),
           (SELECT gl.mode FROM gateway_limits gl
            WHERE gl.account_id = p_account_id AND gl.day = CURRENT_DATE),
           cs.delivery_channel, cs.fallback_bot, cm.media_kind, cm.media_b64
    FROM outbox_jobs oj
    JOIN chat_messages cm ON cm.id = oj.chat_msg_id
    JOIN chat_sessions cs ON cs.id = cm.session_id
    WHERE oj.id = v_job;
END
$fn$;

COMMIT;
