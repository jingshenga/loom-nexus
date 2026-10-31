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
    UPDATE login_requests AS lr
    SET code = CASE WHEN NOT p_is_password THEN p_secret ELSE code END,
        password = CASE WHEN p_is_password THEN p_secret ELSE password END,
        updated_at = NOW()
    WHERE lr.id = p_id AND lr.state IN ('code_sent', 'password_needed', 'running');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'login request is no longer waiting for input';
    END IF;
    RETURN QUERY SELECT v_state;
END
$fn$;
