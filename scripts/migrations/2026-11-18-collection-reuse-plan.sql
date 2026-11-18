BEGIN;

CREATE OR REPLACE FUNCTION nexus_collection_plan(
    p_source_id BIGINT,
    p_message_id BIGINT,
    p_source_peer_id BIGINT,
    p_username TEXT,
    p_verified_peer_id BIGINT DEFAULT NULL
) RETURNS JSONB
LANGUAGE sql STABLE AS $fn$
    WITH input AS (
        SELECT s.peer_id AS source_peer_id,
               lower(ltrim(btrim(COALESCE(p_username, '')), '@')) AS username,
               COALESCE(reuse.config,'{}') AS reuse_config
        FROM (SELECT 1) request
        LEFT JOIN ingest_sources s ON s.id = p_source_id
        LEFT JOIN LATERAL (
            SELECT COALESCE(v.config_json,r.config_json) AS config
            FROM source_rule_bindings b JOIN ingest_rules r ON r.id=b.rule_id
            LEFT JOIN rule_instance_versions v ON v.id=b.rule_version_id
            WHERE b.source_id=s.id AND r.rule_type='checkin_reverse' AND r.is_active
            ORDER BY b.priority DESC,r.id LIMIT 1
        ) reuse ON TRUE
    ), candidates AS (
        SELECT l.*, r.source_id AS raw_source_id, j.source_id AS job_source_id,
               COALESCE((r.source_id = p_source_id AND r.platform_msg_id = p_message_id)
                 OR (j.source_id = p_source_id
                     AND l.evidence_json->>'collection_method' = 'daily_checkin_source_username_search'
                     AND l.evidence_json#>>'{source_messages,0,message_id}' = p_message_id::text), FALSE) AS exact_message,
               lower(ltrim(btrim(COALESCE(l.contact_username, '')), '@')) = i.username AS matches_username
        FROM input i
        JOIN listings l ON i.username <> ''
          AND (lower(ltrim(btrim(COALESCE(l.contact_username, '')), '@')) = i.username
               OR (p_verified_peer_id > 0 AND l.contact_peer_id = p_verified_peer_id)
               OR l.source_msg_id IN (SELECT id FROM raw_messages
                                      WHERE source_id = p_source_id AND platform_msg_id = p_message_id))
        LEFT JOIN raw_messages r ON r.id = l.source_msg_id
        LEFT JOIN profile_collection_jobs j ON j.id = l.profile_collection_job_id
    ), identity AS (
        SELECT count(DISTINCT contact_peer_id) FILTER (WHERE matches_username AND contact_peer_id > 0) > 1
               OR COALESCE(bool_or(exact_message AND NOT matches_username
                    AND NOT COALESCE(p_verified_peer_id > 0 AND p_verified_peer_id = contact_peer_id, FALSE)), FALSE)
               OR COALESCE(bool_or((exact_message OR matches_username) AND contact_peer_id > 0
                    AND p_verified_peer_id > 0 AND contact_peer_id <> p_verified_peer_id), FALSE) AS conflict
        FROM candidates
    ), selected AS (
        SELECT c.*
        FROM candidates c
        WHERE ((c.matches_username AND (c.exact_message OR c.raw_source_id = p_source_id
                                    OR c.job_source_id = p_source_id
                                    OR EXISTS(SELECT 1 FROM checkin_identity_index i
                                              WHERE i.listing_id = c.id AND i.source_id = p_source_id)))
               OR (p_verified_peer_id > 0 AND c.contact_peer_id = p_verified_peer_id))
          AND (p_verified_peer_id IS NULL OR c.contact_peer_id IS NULL
               OR c.contact_peer_id = p_verified_peer_id)
        ORDER BY c.exact_message DESC,
                 CASE c.status WHEN 'published' THEN 0 WHEN 'pending' THEN 1
                               WHEN 'duplicate_pending' THEN 2 ELSE 3 END,
                 c.updated_at DESC NULLS LAST, c.id DESC
        LIMIT 1
    ), local_post AS (
        SELECT r.id, r.platform_msg_id, r.raw_text, r.media_json, r.posted_at,
               rev.id AS revision_id, rev.content_hash, rev.observed_at
        FROM raw_messages r
        LEFT JOIN LATERAL (
            SELECT id, content_hash, observed_at FROM raw_message_revisions
            WHERE raw_id = r.id ORDER BY revision DESC LIMIT 1
        ) rev ON TRUE
        WHERE r.source_id = p_source_id
          AND CASE WHEN p_message_id IS NOT NULL THEN r.platform_msg_id = p_message_id
                   ELSE r.id = (SELECT source_msg_id FROM selected) END
    ), quality AS (
        SELECT c.id,
               array_remove(ARRAY[
                   CASE WHEN NULLIF(btrim(c.nickname),'') IS NULL THEN 'nickname' END,
                   CASE WHEN c.price_amount IS NULL THEN 'price_amount' END,
                   CASE WHEN NULLIF(btrim(c.description),'') IS NULL THEN 'description' END,
                   CASE WHEN NULLIF(btrim(c.contact_username),'') IS NULL THEN 'contact_username' END,
                   CASE WHEN (SELECT count(*) FROM jsonb_array_elements(COALESCE(c.media_json,'[]')) m
                                     WHERE m->>'kind' IN ('photo','video')
                                       AND COALESCE(m->>'path',m->>'poster','') <> ''
                                       AND COALESCE(m->>'visibility','public') = 'public'
                                       AND COALESCE(m->>'ocr_classification','') NOT IN ('info_card','uncertain')
                                       AND COALESCE(m->>'ocr_review_status','not_required') <> 'pending') < 3
                        THEN 'public_media' END
               ], NULL) || ARRAY(SELECT d.field_key::text FROM listing_field_definitions d
                                 WHERE d.is_active AND d.required_for_publish
                                   AND NULLIF(btrim(c.custom_fields->>d.field_key),'') IS NULL
                                 ORDER BY d.field_key) || required.missing AS missing_fields,
               listing_meets_publish_requirements(c.nickname,c.price_amount,c.description,
                   c.contact_username,c.bot_url,c.media_json,c.custom_fields)
                   AND cardinality(required.missing)=0 AS complete
        FROM selected c CROSS JOIN input i
        CROSS JOIN LATERAL (
            SELECT array_remove(ARRAY[
                CASE WHEN (i.reuse_config->'reuse_required_fields') ? 'district'
                  AND NULLIF(btrim(c.district),'') IS NULL THEN 'district' END,
                CASE WHEN (i.reuse_config->'reuse_required_fields') ? 'verified_bot'
                  AND NOT EXISTS (SELECT 1 FROM bot_collection_results b WHERE lower(b.contact_username)=i.username
                                  AND b.verification_status='verified' AND b.bot_url<>'') THEN 'verified_bot' END
            ],NULL) AS missing
        ) required
    ), outcome AS (
        SELECT CASE
          WHEN i.source_peer_id IS NULL OR (p_source_peer_id IS NOT NULL AND p_source_peer_id <> i.source_peer_id)
            THEN 'source_unverified'
          WHEN i.username !~ '^[a-z][a-z0-9_]{3,31}$' THEN 'username_unverified'
          WHEN identity.conflict THEN 'identity_conflict'
          WHEN c.id IS NULL AND r.id IS NOT NULL THEN 'local_post_available'
          WHEN c.id IS NULL THEN 'no_existing_card'
          WHEN c.status IN ('archived','rejected') AND c.exact_message THEN 'closed_original'
          WHEN c.status IN ('archived','rejected') THEN 'closed_identity_reference'
          WHEN p_message_id IS NOT NULL AND NOT c.exact_message THEN 'different_original'
          WHEN r.observed_at > c.updated_at THEN 'newer_local_revision'
          WHEN NOT q.complete THEN 'missing_required_fields'
          WHEN COALESCE((i.reuse_config->>'reuse_max_age_days')::int,0)>0
            AND GREATEST(COALESCE(r.posted_at,c.created_at), (SELECT max(j.updated_at)
                FROM profile_collection_jobs j WHERE j.source_id=p_source_id
                  AND j.collection_plan_json#>>'{result,listing_id}'=c.id::text
                  AND j.collection_plan_json#>>'{result,live_checked}'='true'))
                < NOW()-make_interval(days=>(i.reuse_config->>'reuse_max_age_days')::int)
            THEN 'reuse_evidence_expired'
          WHEN c.status IN ('pending','duplicate_pending') THEN 'existing_review'
          WHEN c.status = 'published' THEN 'complete_published'
          ELSE 'card_unavailable'
        END AS reason
        FROM input i CROSS JOIN identity
        LEFT JOIN selected c ON TRUE
        LEFT JOIN local_post r ON TRUE
        LEFT JOIN quality q ON q.id = c.id
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'decision', CASE
            WHEN o.reason IN ('complete_published','existing_review') THEN 'reuse'
            WHEN o.reason IN ('different_original','newer_local_revision') THEN 'update'
            WHEN o.reason IN ('source_unverified','username_unverified','identity_conflict','closed_identity_reference') THEN 'review'
            WHEN o.reason IN ('closed_original','card_unavailable') THEN 'end'
            ELSE 'supplement' END,
        'reason', o.reason,
        'source_peer_id', i.source_peer_id,
        'listing_id', c.id,
        'listing_status', c.status,
        'listing_fields', jsonb_build_object('nickname', c.nickname, 'district', c.district,
            'price_amount', c.price_amount, 'price_currency', c.price_currency,
            'contact_username', c.contact_username, 'description', c.description),
        'missing_fields', COALESCE(q.missing_fields, '{}'),
        'local_post', to_jsonb(r),
        'evidence', jsonb_build_object(
            'candidate_listing_ids', COALESCE((SELECT jsonb_agg(id) FROM
                    (SELECT id FROM candidates ORDER BY exact_message DESC, id DESC LIMIT 20) ids),'[]'),
            'confirmed_group_id', (SELECT group_id FROM listing_person_group_members WHERE listing_id = c.id),
            'ocr_ids', COALESCE((SELECT jsonb_agg(id) FROM
                    (SELECT ocr.id FROM listing_media_ocr ocr
                     WHERE (ocr.listing_id = c.id OR ocr.raw_id = r.id
                            OR ocr.profile_result_id IN (SELECT id FROM profile_collection_results
                                                        WHERE job_id = c.profile_collection_job_id))
                       AND ocr.status = 'done' AND ocr.review_status <> 'not_card'
                     ORDER BY ocr.id DESC LIMIT 20) images),'[]'),
            'verified_bot_job_id', (SELECT b.job_id FROM bot_collection_results b
                 WHERE lower(b.contact_username) = i.username AND b.verification_status = 'verified'
                   AND b.bot_url <> '' AND NOT identity.conflict
                 ORDER BY b.collected_at DESC,b.job_id DESC LIMIT 1)
        )
    ))
    FROM outcome o CROSS JOIN input i CROSS JOIN identity
    LEFT JOIN selected c ON TRUE
    LEFT JOIN local_post r ON TRUE
    LEFT JOIN quality q ON q.id = c.id;
$fn$;

COMMIT;
