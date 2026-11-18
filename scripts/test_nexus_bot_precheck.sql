-- Run on a disposable copy after the Bot precheck migration.
BEGIN;

DO $test$
DECLARE
    source_id BIGINT;
    account_id BIGINT;
    rule_id BIGINT;
    fixture_job BIGINT;
    row_reason TEXT;
BEGIN
    INSERT INTO bridge_accounts(session_file) VALUES('bot-fixture.session') RETURNING id INTO account_id;
    INSERT INTO ingest_sources(peer_id,assigned_account_id) VALUES(-10033882001,account_id) RETURNING id INTO source_id;
    INSERT INTO ingest_rules(rule_name,rule_type,config_json) VALUES('fixture bot rule','bot_collection',
        '{"precheck_enabled":true,"reuse_verified_days":30,"reuse_no_bot_days":7}') RETURNING id INTO rule_id;
    INSERT INTO source_rule_bindings(source_id,rule_id) VALUES(source_id,rule_id);

    IF nexus_bot_collection_precheck(rule_id,source_id,'') IS DISTINCT FROM 'no_valid_contact'
       OR nexus_bot_collection_precheck(rule_id,source_id,'fixture_botcontact') IS DISTINCT FROM 'no_eligible_card' THEN
        RAISE EXCEPTION 'Incomplete or unavailable card should not be queried';
    END IF;
    INSERT INTO listings(contact_username,contact_peer_id,status) VALUES('fixture_botcontact',33882001,'published');
    IF nexus_bot_collection_precheck(rule_id,source_id,'@Fixture_BotContact') IS NOT NULL THEN
        RAISE EXCEPTION 'Published matching identity should be eligible';
    END IF;

    INSERT INTO profile_collection_jobs(rule_id,source_id,account_id,duty_date,contact_username)
    VALUES(rule_id,source_id,account_id,CURRENT_DATE,'fixture_botcontact') RETURNING id INTO fixture_job;
    INSERT INTO bot_collection_results(job_id,contact_username,bot_url,verification_status)
    VALUES(fixture_job,'fixture_botcontact','https://t.me/fixture_helper_bot','verified');
    row_reason := nexus_bot_collection_precheck(rule_id,source_id,'fixture_botcontact');
    IF row_reason IS DISTINCT FROM 'verified_result_available' THEN
        RAISE EXCEPTION 'Expected a reusable verified result, got %', row_reason;
    END IF;
    UPDATE bot_collection_results b SET bot_url='',verification_status='unverified',
        discovery_json='{"outcome":"no_bot"}' WHERE b.job_id=fixture_job;
    IF nexus_bot_collection_precheck(rule_id,source_id,'fixture_botcontact') IS DISTINCT FROM 'recent_no_bot' THEN
        RAISE EXCEPTION 'Confirmed no-Bot result should be reused';
    END IF;
    UPDATE bot_collection_results b SET discovery_json='{"outcome":"lookup_incomplete"}' WHERE b.job_id=fixture_job;
    IF nexus_bot_collection_precheck(rule_id,source_id,'fixture_botcontact') IS NOT NULL THEN
        RAISE EXCEPTION 'A failed lookup must not be cached as no Bot';
    END IF;
    INSERT INTO listings(contact_username,contact_peer_id,status) VALUES('fixture_botcontact',33882002,'pending');
    IF nexus_bot_collection_precheck(rule_id,source_id,'fixture_botcontact') IS DISTINCT FROM 'contact_conflict' THEN
        RAISE EXCEPTION 'Conflicting peer IDs must be investigated';
    END IF;
END;
$test$;

ROLLBACK;
