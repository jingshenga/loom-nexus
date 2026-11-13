DO $test$
DECLARE
    v_good BIGINT;
    v_disabled BIGINT;
    v_cooling BIGINT;
    v_user BIGINT;
    v_good_session BIGINT;
    v_disabled_session BIGINT;
    v_cooling_session BIGINT;
    v_alias TEXT;
    v_bound INTEGER;
BEGIN
    INSERT INTO bridge_accounts(display_name, session_file, status)
    VALUES ('active-test', 'active-test.session', 'active') RETURNING id INTO v_good;
    INSERT INTO bridge_accounts(display_name, session_file, status)
    VALUES ('disabled-test', 'disabled-test.session', 'disconnected') RETURNING id INTO v_disabled;
    INSERT INTO bridge_accounts(display_name, session_file, status)
    VALUES ('cooling-test', 'cooling-test.session', 'active') RETURNING id INTO v_cooling;

    INSERT INTO site_users(username, password_hash, display_name)
    VALUES ('sender-test', 'not-a-real-hash', '审核昵称') RETURNING id INTO v_user;
    INSERT INTO chat_sessions(user_id, contact_peer_id, assigned_account_id, status, contact_username)
    VALUES (v_user, 81001, v_good, 'open', 'contact-good') RETURNING id INTO v_good_session;
    INSERT INTO chat_sessions(user_id, contact_peer_id, assigned_account_id, status, contact_username)
    VALUES (v_user, 81002, v_disabled, 'open', 'contact-disabled') RETURNING id INTO v_disabled_session;
    INSERT INTO chat_sessions(user_id, contact_peer_id, assigned_account_id, status, contact_username)
    VALUES (v_user, 81003, v_cooling, 'open', 'contact-cooling') RETURNING id INTO v_cooling_session;

    INSERT INTO chat_messages(session_id, client_msg_id, direction, content, status,
                              outbound_alias_snapshot)
    VALUES (v_good_session, 'good', 'out', 'hello', 'queued', '冻结昵称');
    INSERT INTO chat_messages(session_id, client_msg_id, direction, content, status,
                              outbound_alias_snapshot)
    VALUES (v_disabled_session, 'disabled', 'out', 'hello', 'queued', '禁用测试');
    INSERT INTO chat_messages(session_id, client_msg_id, direction, content, status,
                              outbound_alias_snapshot)
    VALUES (v_cooling_session, 'cooling', 'out', 'hello', 'queued', '冷却测试');

    INSERT INTO outbox_jobs(chat_msg_id)
    SELECT id FROM chat_messages WHERE session_id IN (v_good_session, v_disabled_session, v_cooling_session);
    PERFORM account_flood_wait_set(v_cooling, 3600);

    SELECT display_name INTO v_alias FROM outbox_claim_job(v_good) LIMIT 1;
    IF v_alias IS DISTINCT FROM '冻结昵称' THEN
        RAISE EXCEPTION 'queued nickname snapshot was not returned';
    END IF;
    IF EXISTS (SELECT 1 FROM outbox_claim_job(v_disabled)) THEN
        RAISE EXCEPTION 'disabled gateway account claimed a new job';
    END IF;
    IF EXISTS (SELECT 1 FROM outbox_claim_job(v_cooling)) THEN
        RAISE EXCEPTION 'FloodWait account claimed a job before cooldown expired';
    END IF;

    INSERT INTO listings(contact_username, contact_peer_id)
    VALUES ('PeerTest', NULL), ('peertest', 91001);
    v_bound := presence_bind_peer('PEERTEST', 91002);
    IF v_bound <> 1 THEN
        RAISE EXCEPTION 'presence did not bind exactly one empty peer id (bound=%)', v_bound;
    END IF;
    IF (SELECT contact_peer_id FROM listings WHERE contact_username='PeerTest') <> 91002
       OR (SELECT contact_peer_id FROM listings WHERE contact_username='peertest') <> 91001 THEN
        RAISE EXCEPTION 'presence overwrote an existing contact peer id';
    END IF;
END
$test$;
