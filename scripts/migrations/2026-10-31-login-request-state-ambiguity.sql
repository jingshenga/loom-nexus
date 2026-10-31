CREATE OR REPLACE FUNCTION login_request_start(
    p_phone VARCHAR(32),
    p_session_name VARCHAR(64)
) RETURNS TABLE(request_id BIGINT, session_name VARCHAR(64), state VARCHAR(24))
LANGUAGE plpgsql AS $fn$
DECLARE
    v_id BIGINT; v_name VARCHAR(64);
BEGIN
    IF EXISTS (SELECT 1 FROM login_requests lr
               WHERE lr.state IN ('pending', 'running', 'code_sent', 'password_needed')) THEN
        RAISE EXCEPTION 'a login request is already in progress';
    END IF;
    v_name := COALESCE(NULLIF(p_session_name, ''), 'gw');
    INSERT INTO login_requests AS lr (phone, session_name)
    VALUES (p_phone, v_name)
    RETURNING lr.id, lr.session_name INTO v_id, v_name;
    RETURN QUERY SELECT v_id, v_name, 'pending'::VARCHAR(24);
END
$fn$;

CREATE OR REPLACE FUNCTION login_request_set_code(
    p_id BIGINT, p_secret TEXT, p_is_password BOOLEAN
) RETURNS TABLE(state VARCHAR(24))
LANGUAGE plpgsql AS $fn$
DECLARE
    v_state VARCHAR(24);
BEGIN
    SELECT lr.state INTO v_state FROM login_requests lr WHERE lr.id = p_id;
    IF v_state IS NULL THEN
        RAISE EXCEPTION 'login request not found';
    END IF;
    IF v_state NOT IN ('code_sent', 'password_needed', 'running') THEN
        RAISE EXCEPTION 'login request not waiting for input (state=%)', v_state;
    END IF;
    UPDATE login_requests
    SET code = CASE WHEN NOT p_is_password THEN p_secret ELSE code END,
        password = CASE WHEN p_is_password THEN p_secret ELSE password END,
        updated_at = NOW()
    WHERE id = p_id AND state IN ('code_sent', 'password_needed', 'running');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'login request is no longer waiting for input';
    END IF;
    RETURN QUERY SELECT v_state;
END
$fn$;

CREATE OR REPLACE FUNCTION login_request_update_state(
    p_id BIGINT, p_state VARCHAR(24), p_error TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE login_requests
    SET state = p_state, error = p_error, updated_at = NOW()
    WHERE id = p_id AND state <> 'cancelled';
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION login_request_cancel(p_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE login_requests
    SET state = 'cancelled', code = NULL, password = NULL,
        error = 'cancelled by admin', updated_at = NOW()
    WHERE id = p_id
      AND state IN ('pending', 'running', 'code_sent', 'password_needed');
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION login_request_take_secret(
    p_id BIGINT, p_is_password BOOLEAN
) RETURNS TEXT
LANGUAGE plpgsql AS $fn$
DECLARE
    v_secret TEXT;
    v_state VARCHAR(24);
BEGIN
    SELECT lr.state, CASE WHEN p_is_password THEN lr.password ELSE lr.code END
    INTO v_state, v_secret FROM login_requests lr WHERE lr.id = p_id;
    IF v_state = 'cancelled' THEN
        RETURN '__NEXUS_LOGIN_CANCELLED__';
    END IF;
    IF v_secret IS NOT NULL THEN
        UPDATE login_requests
        SET code = CASE WHEN NOT p_is_password THEN NULL ELSE code END,
            password = CASE WHEN p_is_password THEN NULL ELSE password END,
            updated_at = NOW()
        WHERE id = p_id AND state <> 'cancelled';
    END IF;
    RETURN v_secret;
END
$fn$;
