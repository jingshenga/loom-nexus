BEGIN;

ALTER TABLE bot_collection_results ADD COLUMN IF NOT EXISTS discovery_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE bot_collection_audit ADD COLUMN IF NOT EXISTS chosen_bot_url TEXT;

CREATE OR REPLACE FUNCTION nexus_bot_collection_precheck(p_rule_id BIGINT, p_source_id BIGINT, p_username TEXT)
RETURNS TEXT LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    policy JSONB;
    username TEXT := lower(ltrim(btrim(COALESCE(p_username,'')),'@'));
    verified_days INT;
    no_bot_days INT;
BEGIN
    SELECT COALESCE(v.config_json,r.config_json) INTO policy
    FROM ingest_rules r JOIN source_rule_bindings b ON b.rule_id=r.id
    LEFT JOIN rule_instance_versions v ON v.id=b.rule_version_id
    WHERE r.id=p_rule_id AND b.source_id=p_source_id AND r.rule_type='bot_collection' AND r.is_active;
    IF COALESCE(policy->'precheck_enabled','false') <> 'true'::jsonb THEN RETURN NULL; END IF;
    IF username !~ '^[a-z][a-z0-9_]{3,31}$' THEN RETURN 'no_valid_contact'; END IF;
    IF (SELECT count(DISTINCT l.contact_peer_id)>1 FROM listings l
        WHERE lower(ltrim(btrim(l.contact_username),'@'))=username AND l.contact_peer_id>0) THEN
        RETURN 'contact_conflict';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM listings l WHERE lower(ltrim(btrim(l.contact_username),'@'))=username
          AND (l.status='published' OR (l.status='pending' AND policy->'allow_pending_cards'='true'::jsonb
                AND listing_meets_publish_requirements(l.nickname,l.price_amount,l.description,l.contact_username,
                    l.bot_url,l.media_json,l.custom_fields)))) THEN
        RETURN 'no_eligible_card';
    END IF;
    verified_days := COALESCE((policy->>'reuse_verified_days')::int,30);
    no_bot_days := COALESCE((policy->>'reuse_no_bot_days')::int,7);
    IF verified_days > 0 AND EXISTS (SELECT 1 FROM bot_collection_results b
        WHERE lower(b.contact_username)=username AND b.verification_status='verified' AND b.bot_url<>''
          AND b.collected_at>=NOW()-make_interval(days=>verified_days)) THEN
        RETURN 'verified_result_available';
    END IF;
    IF no_bot_days > 0 AND EXISTS (SELECT 1 FROM bot_collection_results b
        WHERE lower(b.contact_username)=username AND b.discovery_json->>'outcome'='no_bot'
          AND b.collected_at>=NOW()-make_interval(days=>no_bot_days)) THEN
        RETURN 'recent_no_bot';
    END IF;
    RETURN NULL;
END;
$fn$;

COMMIT;
