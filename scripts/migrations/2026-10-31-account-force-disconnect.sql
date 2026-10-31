CREATE TABLE IF NOT EXISTS bridge_disconnect_requests (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES bridge_accounts(id) ON DELETE CASCADE,
    action VARCHAR(16) NOT NULL DEFAULT 'disconnect',
    state VARCHAR(16) NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
ALTER TABLE bridge_disconnect_requests
    ADD COLUMN IF NOT EXISTS action VARCHAR(16) NOT NULL DEFAULT 'disconnect';
CREATE INDEX IF NOT EXISTS bridge_disconnect_requests_pending_idx
    ON bridge_disconnect_requests (id) WHERE state = 'pending';

CREATE OR REPLACE FUNCTION account_force_disconnect(p_account_id BIGINT)
RETURNS TABLE(request_id BIGINT, closed_sessions INT, bound_sources INT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_request_id BIGINT;
    v_closed INT := 0;
    v_sources INT := 0;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM bridge_accounts WHERE id = p_account_id) THEN
        RETURN;
    END IF;
    UPDATE chat_sessions SET status = 'closed', last_msg_at = COALESCE(last_msg_at, NOW())
    WHERE assigned_account_id = p_account_id AND status = 'open';
    GET DIAGNOSTICS v_closed = ROW_COUNT;
    SELECT count(*) INTO v_sources
    FROM ingest_sources s
    WHERE s.assigned_account_id = p_account_id AND s.is_active;
    UPDATE bridge_accounts SET status = 'disconnected', last_active_at = NOW()
    WHERE id = p_account_id;
    INSERT INTO bridge_disconnect_requests (account_id, action)
    VALUES (p_account_id, 'disconnect')
    RETURNING id INTO v_request_id;
    RETURN QUERY SELECT v_request_id, v_closed, v_sources;
END
$fn$;

CREATE OR REPLACE FUNCTION account_activate_request(p_account_id BIGINT)
RETURNS TABLE(request_id BIGINT)
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM bridge_accounts
        WHERE id = p_account_id AND status = 'disconnected'
    ) THEN
        RETURN;
    END IF;
    INSERT INTO bridge_disconnect_requests (account_id, action)
    VALUES (p_account_id, 'activate')
    RETURNING id INTO request_id;
    RETURN NEXT;
END
$fn$;
