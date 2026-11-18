BEGIN;

-- Claim the same original as live intake, without requeueing it for Legacy extraction.
CREATE OR REPLACE FUNCTION nexus_stage_checkin_original(
    p_job_id BIGINT, p_message_id BIGINT, p_source_peer_id BIGINT,
    p_sender_peer_id BIGINT, p_grouped_id BIGINT, p_posted_at TIMESTAMPTZ,
    p_description TEXT, p_media JSONB, p_evidence JSONB, p_expected_revision_id BIGINT DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql AS $fn$
DECLARE
    job profile_collection_jobs%ROWTYPE;
    original raw_messages%ROWTYPE;
    revision raw_message_revisions%ROWTYPE;
    existing_listing BIGINT;
    active_review BIGINT;
    original_ref JSONB;
    plan JSONB;
    outcome JSONB;
BEGIN
    SELECT j.* INTO job FROM profile_collection_jobs j
    JOIN ingest_rules rule ON rule.id=j.rule_id AND rule.rule_type='checkin_reverse' AND rule.is_active
    JOIN ingest_sources s ON s.id=j.source_id AND s.peer_id=p_source_peer_id AND s.is_active
    WHERE j.id=p_job_id FOR UPDATE OF j;
    IF NOT FOUND OR p_message_id IS NULL OR p_message_id <= 0
       OR NOT EXISTS (SELECT 1 FROM app_settings WHERE key='collection_reuse_enabled' AND value='1')
       OR (job.rule_version_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM source_rule_bindings b WHERE b.source_id=job.source_id
                AND b.rule_id=job.rule_id AND b.rule_version_id=job.rule_version_id)) THEN
        RETURN jsonb_build_object('status','needs_review','reason','collection_context_changed');
    END IF;

    INSERT INTO raw_messages(source_id,platform_msg_id,sender_peer_id,grouped_id,posted_at,raw_text,media_json,extract_state)
    SELECT job.source_id,p_message_id,p_sender_peer_id,p_grouped_id,p_posted_at,
           COALESCE(p_description,''),COALESCE(p_media,'[]'),'dropped'
    WHERE p_expected_revision_id IS NULL
    ON CONFLICT (source_id,platform_msg_id) DO NOTHING;
    SELECT * INTO original FROM raw_messages
    WHERE source_id=job.source_id AND platform_msg_id=p_message_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status','needs_review','reason','local_original_missing');
    END IF;
    SELECT rr.* INTO revision FROM raw_message_revisions rr WHERE rr.raw_id=original.id ORDER BY rr.revision DESC LIMIT 1;
    original_ref := jsonb_strip_nulls(jsonb_build_object('id',original.id,'platform_msg_id',p_message_id,
                                  'revision_id',revision.id,'content_hash',revision.content_hash));
    plan := nexus_collection_plan(job.source_id,p_message_id,p_source_peer_id,job.contact_username);

    SELECT l.id INTO existing_listing FROM listings l WHERE l.source_msg_id=original.id
    ORDER BY CASE l.status WHEN 'published' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,l.id DESC LIMIT 1;
    SELECT q.id INTO active_review FROM rule_active_review_queue q WHERE q.raw_id=original.id
    AND (q.content_hash=revision.content_hash OR q.status='open') ORDER BY q.id DESC LIMIT 1;

    IF p_expected_revision_id IS NOT NULL AND revision.id IS DISTINCT FROM p_expected_revision_id THEN
        outcome := jsonb_build_object('status','needs_review','reason','original_revision_changed');
    ELSIF plan->>'decision'='review' THEN
        outcome := jsonb_build_object('status','needs_review','reason',plan->>'reason');
    ELSIF COALESCE(original.raw_text,'') IS DISTINCT FROM COALESCE(p_description,'') THEN
        outcome := jsonb_build_object('status','needs_review','reason','original_text_differs');
    ELSIF existing_listing IS NOT NULL OR plan->>'decision' IN ('reuse','end') THEN
        outcome := jsonb_build_object('status','skipped','reason','existing_original',
                                      'listing_id',COALESCE(existing_listing,(plan->>'listing_id')::bigint));
    ELSIF active_review IS NOT NULL THEN
        outcome := jsonb_build_object('status','skipped','reason','existing_rule_review','rule_review_id',active_review);
    ELSIF original.extract_state IN ('new','processing') THEN
        outcome := jsonb_build_object('status','skipped','reason','core_owns_original');
    ELSIF NOT (COALESCE(p_evidence->>'nickname','') <> '' OR COALESCE(p_evidence->>'district','') <> ''
               OR jsonb_array_length(COALESCE(original.media_json,'[]')) > 0) THEN
        outcome := jsonb_build_object('status','not_found','reason','no_usable_card_content');
    ELSE
        INSERT INTO listings(source_msg_id,profile_collection_job_id,contact_username,nickname,district,
                             description,media_json,evidence_json,status,confidence,auto_published)
        VALUES(original.id,job.id,job.contact_username,NULLIF(p_evidence->>'nickname',''),
            NULLIF(p_evidence->>'district',''),original.raw_text,original.media_json,
            COALESCE(p_evidence,'{}') || jsonb_build_object('original',original_ref),
            CASE WHEN plan->>'listing_id' IS NULL THEN 'pending' ELSE 'duplicate_pending' END,0.5,FALSE)
        ON CONFLICT (profile_collection_job_id) WHERE profile_collection_job_id IS NOT NULL DO NOTHING
        RETURNING id INTO existing_listing;
        IF existing_listing IS NULL THEN
            SELECT id INTO existing_listing FROM listings WHERE profile_collection_job_id=job.id;
            outcome := jsonb_build_object('status','needs_review','reason','job_has_other_original','listing_id',existing_listing);
        ELSE
            outcome := jsonb_build_object('status','fetched','reason','candidate_created','listing_id',existing_listing);
        END IF;
    END IF;
    UPDATE profile_collection_jobs SET collection_plan_json=collection_plan_json
        || jsonb_build_object('original',original_ref,'result',outcome)
    WHERE id=job.id;
    RETURN outcome;
END;
$fn$;

COMMIT;
