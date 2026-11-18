-- Run on a disposable copy after the three collection reuse migrations.
BEGIN;

DO $test$
DECLARE
    source_id BIGINT;
    account_id BIGINT;
    rule_id BIGINT;
    first_job BIGINT;
    second_job BIGINT;
    original_id BIGINT;
    outcome JSONB;
    evidence JSONB := '{"nickname":"fixture card","review_bucket":"checkin_supplement"}';
BEGIN
    INSERT INTO app_settings(key,value) VALUES('collection_reuse_enabled','1')
    ON CONFLICT(key) DO UPDATE SET value='1';
    INSERT INTO bridge_accounts(session_file) VALUES('checkin-fixture.session') RETURNING id INTO account_id;
    INSERT INTO ingest_sources(peer_id) VALUES(-10033990001) RETURNING id INTO source_id;
    INSERT INTO ingest_rules(rule_name,rule_type,config_json) VALUES('checkin fixture','checkin_reverse','{}') RETURNING id INTO rule_id;
    INSERT INTO profile_collection_jobs(rule_id,source_id,account_id,duty_date,contact_username)
    VALUES(rule_id,source_id,account_id,CURRENT_DATE,'fixture_stage') RETURNING id INTO first_job;
    INSERT INTO profile_collection_jobs(rule_id,source_id,account_id,duty_date,contact_username)
    VALUES(rule_id,source_id,account_id,CURRENT_DATE+1,'fixture_stage') RETURNING id INTO second_job;

    outcome := nexus_stage_checkin_original(first_job,101,-10033990001,121,NULL,NOW(),
        '昵称：fixture @fixture_stage','[]',evidence);
    IF outcome->>'reason' <> 'candidate_created' THEN
        RAISE EXCEPTION 'Expected staged pending card: %', outcome;
    END IF;
    SELECT l.source_msg_id INTO original_id FROM listings l
    WHERE l.id=(outcome->>'listing_id')::bigint AND l.status='pending' AND l.auto_published IS FALSE;
    IF original_id IS NULL OR (SELECT extract_state FROM raw_messages WHERE id=original_id) <> 'dropped' THEN
        RAISE EXCEPTION 'Original must be durable and excluded from Legacy extraction';
    END IF;

    outcome := nexus_stage_checkin_original(second_job,101,-10033990001,121,NULL,NOW(),
        '昵称：fixture @fixture_stage','[]',evidence);
    IF outcome->>'reason' <> 'existing_original' OR
      (SELECT count(*) FROM listings WHERE source_msg_id=original_id) <> 1 THEN
        RAISE EXCEPTION 'A second job must reuse the same original: %', outcome;
    END IF;

    outcome := nexus_stage_checkin_original(second_job,101,-10033990001,121,NULL,NOW(),
        'Changed original @fixture_stage','[]',evidence);
    IF outcome->>'reason' <> 'original_text_differs' THEN
        RAISE EXCEPTION 'Divergent original text must not overwrite stored source material: %', outcome;
    END IF;

    INSERT INTO raw_messages(source_id,platform_msg_id,raw_text,extract_state)
    VALUES(source_id,102,'昵称：fixture @fixture_stage','processing');
    outcome := nexus_stage_checkin_original(second_job,102,-10033990001,121,NULL,NOW(),
        '昵称：fixture @fixture_stage','[]',evidence);
    IF outcome->>'reason' <> 'core_owns_original' THEN
        RAISE EXCEPTION 'Bridge must leave an in-flight original to Core: %', outcome;
    END IF;

    UPDATE app_settings SET value='0' WHERE key='collection_reuse_enabled';
    outcome := nexus_stage_checkin_original(second_job,103,-10033990001,121,NULL,NOW(),
        '昵称：fixture @fixture_stage','[]',evidence);
    IF outcome->>'reason' <> 'collection_context_changed' THEN
        RAISE EXCEPTION 'Switch-off must protect jobs finishing late: %', outcome;
    END IF;
END;
$test$;

ROLLBACK;
