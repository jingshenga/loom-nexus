BEGIN;
DO $test$
DECLARE
    v_user BIGINT;
    v_other BIGINT;
    v_account BIGINT;
    v_inflight_session BIGINT;
    v_delete_session BIGINT;
    v_message BIGINT;
    v_duplicate_message BIGINT;
    v_status TEXT;
    v_alias TEXT;
    v_delete_status TEXT;
BEGIN
    INSERT INTO site_users(username,password_hash,display_name)
    VALUES ('business_test_a','test','站内名') RETURNING id INTO v_user;
    INSERT INTO site_users(username,password_hash,display_name)
    VALUES ('business_test_b','test','另一站内名') RETURNING id INTO v_other;
    INSERT INTO bridge_accounts(account_uid,display_name,username,session_file)
    VALUES (987650001,'test','test','business_test.session') RETURNING id INTO v_account;

    SELECT outcome INTO v_status FROM chat_alias_update(v_user,'小北');
    IF v_status <> 'UPDATED' THEN RAISE EXCEPTION 'initial alias was not accepted: %',v_status; END IF;
    SELECT outcome INTO v_status FROM chat_alias_update(v_user,'小南');
    IF v_status <> 'TOO_SOON' THEN RAISE EXCEPTION 'alias cooldown missing: %',v_status; END IF;
    SELECT outcome INTO v_status FROM chat_alias_update(v_other,'小北');
    IF v_status <> 'TAKEN' THEN RAISE EXCEPTION 'normalized alias was not unique: %',v_status; END IF;

    INSERT INTO chat_sessions(user_id,contact_peer_id,contact_username,assigned_account_id)
    VALUES (v_user,987650101,'contact_a',v_account) RETURNING id INTO v_inflight_session;
    SELECT message_id INTO v_message
    FROM chat_enqueue_out(v_inflight_session,v_user,'client-a','第一条',NULL,NULL);
    SELECT message_id INTO v_duplicate_message
    FROM chat_enqueue_out(v_inflight_session,v_user,'client-a','重试重复请求',NULL,NULL);
    IF v_message <> v_duplicate_message THEN RAISE EXCEPTION 'client_msg_id was not idempotent'; END IF;
    SELECT display_name INTO v_alias FROM outbox_claim_job(v_account) LIMIT 1;
    IF v_alias <> '小北' THEN RAISE EXCEPTION 'outbox alias snapshot missing: %',v_alias; END IF;
    SELECT outcome INTO v_delete_status FROM chat_delete_session(v_inflight_session,v_user);
    IF v_delete_status <> 'IN_FLIGHT' THEN RAISE EXCEPTION 'delete raced an in-flight send: %',v_delete_status; END IF;

    INSERT INTO chat_sessions(user_id,contact_peer_id,contact_username,assigned_account_id)
    VALUES (v_user,987650102,'contact_b',v_account) RETURNING id INTO v_delete_session;
    PERFORM * FROM chat_enqueue_out(v_delete_session,v_user,'client-b','queued text',NULL,NULL);
    SELECT outcome INTO v_delete_status FROM chat_delete_session(v_delete_session,v_user);
    IF v_delete_status <> 'DELETED' THEN RAISE EXCEPTION 'queued conversation was not safely deleted: %',v_delete_status; END IF;
    IF EXISTS (SELECT 1 FROM chat_messages WHERE session_id=v_delete_session) THEN
        RAISE EXCEPTION 'deleted conversation retained message content';
    END IF;
END
$test$;
ROLLBACK;
