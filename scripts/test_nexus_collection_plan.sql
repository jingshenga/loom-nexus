-- Run on a disposable copy of the Nexus schema after the collection-plan migration.
BEGIN;

DO $test$
DECLARE
    source_id BIGINT;
    other_source_id BIGINT;
    raw_id BIGINT;
    card_id BIGINT;
    plan JSONB;
    media JSONB := '[{"kind":"photo","path":"one.jpg"},{"kind":"photo","path":"two.jpg"},{"kind":"photo","path":"three.jpg"}]';
BEGIN
    INSERT INTO ingest_sources(peer_id,title) VALUES (-10012340001,'fixture source') RETURNING id INTO source_id;
    INSERT INTO ingest_sources(peer_id,title) VALUES (-10012340002,'fixture alternate') RETURNING id INTO other_source_id;
    INSERT INTO raw_messages(source_id,platform_msg_id,raw_text,media_json)
    VALUES (source_id,501,'TG: @fixture_contact',media) RETURNING id INTO raw_id;

    plan := nexus_collection_plan(source_id,501,-10012340001,'@Fixture_Contact');
    IF plan->>'reason' <> 'local_post_available' OR plan#>>'{local_post,id}' IS DISTINCT FROM raw_id::text THEN
        RAISE EXCEPTION 'Expected local original and no card: %', plan;
    END IF;

    INSERT INTO listings(source_msg_id,contact_peer_id,contact_username,nickname,price_amount,description,media_json,status)
    VALUES (raw_id,12340001,'fixture_contact','fixture name',100,'fixture text',media,'published') RETURNING id INTO card_id;

    plan := nexus_collection_plan(source_id,501,-10012340001,' @Fixture_Contact ');
    IF plan->>'reason' <> 'complete_published' OR plan->>'listing_id' IS DISTINCT FROM card_id::text THEN
        RAISE EXCEPTION 'Expected direct reuse of a complete published card: %', plan;
    END IF;
    IF nexus_collection_plan(source_id,501,-10012340002,'fixture_contact')->>'reason' <> 'source_unverified' THEN
        RAISE EXCEPTION 'Wrong source peer should be blocked';
    END IF;
    IF nexus_collection_plan(source_id,501,-10012340001,'fixture_other')->>'reason' <> 'identity_conflict' THEN
        RAISE EXCEPTION 'Wrong contact for known original should be blocked';
    END IF;
    IF nexus_collection_plan(source_id,502,NULL,'fixture_contact')->>'reason' <> 'different_original' THEN
        RAISE EXCEPTION 'Another original must be compared before reuse';
    END IF;
    IF nexus_collection_plan(other_source_id,NULL,NULL,'fixture_contact')->>'reason' <> 'no_existing_card'
       OR nexus_collection_plan(other_source_id,NULL,NULL,'fixture_contact',12340001)->>'reason' <> 'complete_published' THEN
        RAISE EXCEPTION 'Cross-source identity requires the verified peer';
    END IF;

    UPDATE listings SET status='pending', price_amount=NULL WHERE id=card_id;
    plan := nexus_collection_plan(source_id,NULL,NULL,'fixture_contact');
    IF plan->>'reason' <> 'missing_required_fields' OR NOT ((plan->'missing_fields') ? 'price_amount') THEN
        RAISE EXCEPTION 'Expected an existing pending card with a price gap: %', plan;
    END IF;
    UPDATE listings SET price_amount=100 WHERE id=card_id;
    IF nexus_collection_plan(source_id,NULL,NULL,'fixture_contact')->>'reason' <> 'existing_review' THEN
        RAISE EXCEPTION 'Complete pending card must keep its existing review';
    END IF;

    UPDATE listings SET status='archived' WHERE id=card_id;
    IF nexus_collection_plan(source_id,501,-10012340001,'fixture_contact')->>'reason' <> 'closed_original'
       OR nexus_collection_plan(source_id,NULL,NULL,'fixture_contact')->>'reason' <> 'closed_identity_reference' THEN
        RAISE EXCEPTION 'Archived card must not reopen';
    END IF;

    INSERT INTO listings(contact_peer_id,contact_username,status) VALUES (12340002,'fixture_contact','pending');
    IF nexus_collection_plan(source_id,501,-10012340001,'fixture_contact')->>'reason' <> 'identity_conflict' THEN
        RAISE EXCEPTION 'Conflicting known peers must be surfaced';
    END IF;
END;
$test$;

ROLLBACK;
