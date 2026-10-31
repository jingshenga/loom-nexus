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
    INSERT INTO login_requests (phone, session_name)
    VALUES (p_phone, v_name)
    RETURNING id, session_name INTO v_id, v_name;
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
    WHERE id = p_id;
    RETURN QUERY SELECT v_state;
END
$fn$;
