-- Nexus V3 2A: immutable rule snapshots and shadow-only routing foundations.
-- Additive migration. Existing sources remain on legacy execution semantics.
BEGIN;

ALTER TABLE ingest_sources
    ADD COLUMN IF NOT EXISTS router_mode VARCHAR(12) NOT NULL DEFAULT 'legacy'
    CHECK (router_mode IN ('legacy', 'shadow', 'active'));

CREATE TABLE IF NOT EXISTS rule_templates (
    id BIGSERIAL PRIMARY KEY,
    template_name VARCHAR(128) NOT NULL UNIQUE,
    rule_type VARCHAR(24) NOT NULL CHECK (rule_type IN (
        'listing','on_duty','profile_collection','bot_collection','checkin_reverse'
    )),
    description TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(128) NOT NULL DEFAULT 'admin_api',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rule_template_versions (
    id BIGSERIAL PRIMARY KEY,
    template_id BIGINT NOT NULL REFERENCES rule_templates(id) ON DELETE CASCADE,
    version INT NOT NULL CHECK (version > 0),
    config_json JSONB NOT NULL,
    config_hash VARCHAR(64) NOT NULL,
    hash_algorithm VARCHAR(12) NOT NULL DEFAULT 'sha256',
    schema_version INT NOT NULL DEFAULT 1,
    parser_key VARCHAR(64) NOT NULL,
    created_by VARCHAR(128) NOT NULL,
    change_summary VARCHAR(512) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(template_id, version)
);

CREATE TABLE IF NOT EXISTS rule_instance_versions (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES ingest_rules(id) ON DELETE CASCADE,
    version INT NOT NULL CHECK (version > 0),
    rule_type VARCHAR(24) NOT NULL,
    config_json JSONB NOT NULL,
    config_hash VARCHAR(64) NOT NULL,
    hash_algorithm VARCHAR(12) NOT NULL,
    schema_version INT NOT NULL DEFAULT 1,
    parser_key VARCHAR(64) NOT NULL,
    template_version_id BIGINT REFERENCES rule_template_versions(id) ON DELETE SET NULL,
    snapshot_origin VARCHAR(24) NOT NULL CHECK (snapshot_origin IN (
        'baseline_capture','create','edit','copy','template_instance','rollback'
    )),
    history_complete BOOLEAN NOT NULL DEFAULT TRUE,
    created_by VARCHAR(128) NOT NULL,
    change_summary VARCHAR(512) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(rule_id, version)
);

ALTER TABLE source_rule_bindings
    ADD COLUMN IF NOT EXISTS rule_version_id BIGINT REFERENCES rule_instance_versions(id) ON DELETE RESTRICT;
CREATE TABLE IF NOT EXISTS source_rule_binding_policies (
    source_id BIGINT NOT NULL,
    rule_id BIGINT NOT NULL,
    purpose VARCHAR(64) NOT NULL,
    match_group VARCHAR(64) NOT NULL DEFAULT 'default',
    match_mode VARCHAR(32) NOT NULL DEFAULT 'legacy_single_selected'
        CHECK (match_mode IN ('legacy_single_selected','exclusive_first_valid',
                              'fallback_on_no_match','fallback_on_no_structure','additive')),
    fallback_order INT NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY(source_id, rule_id, purpose),
    FOREIGN KEY(source_id, rule_id)
        REFERENCES source_rule_bindings(source_id, rule_id) ON DELETE CASCADE
);

-- Capture only the configuration currently observable. This is not a claim
-- that older overwritten versions have been reconstructed.
INSERT INTO rule_instance_versions (
    rule_id, version, rule_type, config_json, config_hash, hash_algorithm,
    parser_key, snapshot_origin, history_complete, created_by, change_summary
)
SELECT r.id, GREATEST(COALESCE(r.version, 1), 1), r.rule_type, r.config_json,
       md5(r.config_json::text), 'md5',
       CASE r.rule_type
           WHEN 'listing' THEN 'ast_rules_v1'
           WHEN 'on_duty' THEN 'on_duty_v1'
           WHEN 'profile_collection' THEN 'profile_collection_v2'
           WHEN 'bot_collection' THEN 'bot_collection_v1'
           WHEN 'checkin_reverse' THEN 'checkin_reverse_v2'
           ELSE 'ast_rules_v1'
       END,
       'baseline_capture', FALSE, 'migration',
       '当前配置基线快照；更早历史版本未知'
FROM ingest_rules r
ON CONFLICT (rule_id, version) DO NOTHING;

UPDATE source_rule_bindings b
SET rule_version_id = v.id
FROM ingest_rules r
JOIN rule_instance_versions v ON v.rule_id = r.id
                              AND v.version = GREATEST(COALESCE(r.version, 1), 1)
WHERE r.id = b.rule_id AND b.rule_version_id IS NULL;

INSERT INTO source_rule_binding_policies(source_id, rule_id, purpose, match_group, match_mode)
SELECT b.source_id, b.rule_id, r.rule_type, 'default', 'legacy_single_selected'
FROM source_rule_bindings b
JOIN ingest_rules r ON r.id = b.rule_id
ON CONFLICT (source_id, rule_id, purpose) DO NOTHING;

ALTER TABLE raw_messages
    ADD COLUMN IF NOT EXISTS rule_version_id BIGINT REFERENCES rule_instance_versions(id) ON DELETE SET NULL;
ALTER TABLE profile_collection_jobs
    ADD COLUMN IF NOT EXISTS rule_version_id BIGINT REFERENCES rule_instance_versions(id) ON DELETE SET NULL;
ALTER TABLE duty_collection_jobs
    ADD COLUMN IF NOT EXISTS rule_version_id BIGINT REFERENCES rule_instance_versions(id) ON DELETE SET NULL;

-- Pin work that was already queued before the version columns existed. The
-- migration snapshot is the best observable baseline; older history remains
-- explicitly marked incomplete on rule_instance_versions.
UPDATE raw_messages rm
SET rule_version_id = COALESCE(
    (SELECT b.rule_version_id
     FROM source_rule_bindings b JOIN ingest_rules r ON r.id=b.rule_id
     WHERE b.source_id=rm.source_id AND r.is_active AND r.rule_type='listing'
     ORDER BY b.priority DESC,r.id LIMIT 1),
    (SELECT v.id FROM ingest_rules r JOIN rule_instance_versions v
       ON v.rule_id=r.id AND v.version=GREATEST(COALESCE(r.version,1),1)
     WHERE r.rule_name='default-rule' AND r.is_active LIMIT 1)
)
WHERE rm.rule_version_id IS NULL AND rm.extract_state IN ('new','processing');

UPDATE profile_collection_jobs j
SET rule_version_id = COALESCE(
    (SELECT b.rule_version_id FROM source_rule_bindings b
     WHERE b.source_id=j.source_id AND b.rule_id=j.rule_id),
    (SELECT v.id FROM rule_instance_versions v
     WHERE v.rule_id=j.rule_id ORDER BY v.version DESC LIMIT 1)
)
WHERE j.rule_version_id IS NULL AND j.status IN ('pending','processing','deferred');

UPDATE duty_collection_jobs j
SET rule_version_id = COALESCE(
    (SELECT b.rule_version_id FROM source_rule_bindings b
     WHERE b.source_id=j.source_id AND b.rule_id=j.rule_id),
    (SELECT v.id FROM rule_instance_versions v
     WHERE v.rule_id=j.rule_id ORDER BY v.version DESC LIMIT 1)
)
WHERE j.rule_version_id IS NULL AND j.status IN ('pending','processing','deferred');

CREATE OR REPLACE FUNCTION pin_collection_job_rule_version() RETURNS TRIGGER
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.rule_version_id IS NULL THEN
        SELECT COALESCE(b.rule_version_id, v.id) INTO NEW.rule_version_id
        FROM ingest_rules r
        LEFT JOIN source_rule_bindings b ON b.rule_id=r.id AND b.source_id=NEW.source_id
        LEFT JOIN rule_instance_versions v ON v.rule_id=r.id
            AND v.version=GREATEST(COALESCE(r.version,1),1)
        WHERE r.id=NEW.rule_id;
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS trg_profile_job_pin_rule_version ON profile_collection_jobs;
CREATE TRIGGER trg_profile_job_pin_rule_version
BEFORE INSERT ON profile_collection_jobs
FOR EACH ROW EXECUTE FUNCTION pin_collection_job_rule_version();
DROP TRIGGER IF EXISTS trg_duty_job_pin_rule_version ON duty_collection_jobs;
CREATE TRIGGER trg_duty_job_pin_rule_version
BEFORE INSERT ON duty_collection_jobs
FOR EACH ROW EXECUTE FUNCTION pin_collection_job_rule_version();

CREATE OR REPLACE FUNCTION pin_raw_message_rule_version() RETURNS TRIGGER
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.rule_version_id IS NULL THEN
        SELECT COALESCE(b.rule_version_id, v.id) INTO NEW.rule_version_id
        FROM source_rule_bindings b
        JOIN ingest_rules r ON r.id=b.rule_id AND r.is_active AND r.rule_type='listing'
        LEFT JOIN rule_instance_versions v ON v.rule_id=r.id
            AND v.version=GREATEST(COALESCE(r.version,1),1)
        WHERE b.source_id=NEW.source_id
        ORDER BY b.priority DESC,r.id LIMIT 1;
        IF NEW.rule_version_id IS NULL THEN
            SELECT v.id INTO NEW.rule_version_id
            FROM ingest_rules r JOIN rule_instance_versions v ON v.rule_id=r.id
                AND v.version=GREATEST(COALESCE(r.version,1),1)
            WHERE r.rule_name='default-rule' AND r.is_active LIMIT 1;
        END IF;
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS trg_raw_message_pin_rule_version ON raw_messages;
CREATE TRIGGER trg_raw_message_pin_rule_version
BEFORE INSERT ON raw_messages
FOR EACH ROW EXECUTE FUNCTION pin_raw_message_rule_version();

CREATE TABLE IF NOT EXISTS raw_message_revisions (
    id BIGSERIAL PRIMARY KEY,
    raw_id BIGINT NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
    revision INT NOT NULL CHECK (revision > 0),
    content_hash VARCHAR(32) NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    media_json JSONB NOT NULL DEFAULT '[]',
    posted_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observed_reason VARCHAR(24) NOT NULL CHECK (observed_reason IN ('ingest','edit','media_update')),
    UNIQUE(raw_id, revision),
    UNIQUE(raw_id, content_hash)
);

CREATE OR REPLACE FUNCTION capture_raw_message_revision() RETURNS TRIGGER
LANGUAGE plpgsql AS $fn$
DECLARE
    v_hash TEXT;
    v_revision INT;
    v_reason VARCHAR(24);
BEGIN
    v_hash := md5(jsonb_build_object(
        'raw_text', COALESCE(NEW.raw_text, ''),
        'media_json', COALESCE(NEW.media_json, '[]'::jsonb),
        'posted_at', NEW.posted_at
    )::text);
    IF TG_OP = 'INSERT' THEN
        v_reason := 'ingest';
    ELSE
        IF NEW.raw_text IS NOT DISTINCT FROM OLD.raw_text
           AND NEW.media_json IS NOT DISTINCT FROM OLD.media_json
           AND NEW.posted_at IS NOT DISTINCT FROM OLD.posted_at THEN
            RETURN NEW;
        END IF;
        v_reason := CASE WHEN NEW.raw_text IS DISTINCT FROM OLD.raw_text
                         THEN 'edit' ELSE 'media_update' END;
    END IF;
    IF EXISTS (SELECT 1 FROM raw_message_revisions WHERE raw_id = NEW.id AND content_hash = v_hash) THEN
        RETURN NEW;
    END IF;
    SELECT COALESCE(max(revision), 0) + 1 INTO v_revision
    FROM raw_message_revisions WHERE raw_id = NEW.id;
    INSERT INTO raw_message_revisions (
        raw_id, revision, content_hash, raw_text, media_json, posted_at, observed_reason
    ) VALUES (
        NEW.id, v_revision, v_hash, COALESCE(NEW.raw_text, ''),
        COALESCE(NEW.media_json, '[]'::jsonb), NEW.posted_at, v_reason
    );
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS trg_raw_message_revision ON raw_messages;
CREATE TRIGGER trg_raw_message_revision
AFTER INSERT OR UPDATE OF raw_text, media_json, posted_at ON raw_messages
FOR EACH ROW EXECUTE FUNCTION capture_raw_message_revision();

CREATE TABLE IF NOT EXISTS rule_parse_runs (
    id BIGSERIAL PRIMARY KEY,
    raw_revision_id BIGINT NOT NULL REFERENCES raw_message_revisions(id) ON DELETE CASCADE,
    purpose VARCHAR(64) NOT NULL,
    rule_id BIGINT REFERENCES ingest_rules(id) ON DELETE SET NULL,
    rule_version_id BIGINT REFERENCES rule_instance_versions(id) ON DELETE SET NULL,
    parser_key VARCHAR(64) NOT NULL,
    run_reason VARCHAR(24) NOT NULL CHECK (run_reason IN ('legacy','shadow','dry_run','reparse')),
    reparse_request_id UUID,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    trace_json JSONB NOT NULL DEFAULT '{}',
    result_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS rule_shadow_comparisons (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES ingest_sources(id) ON DELETE CASCADE,
    raw_id BIGINT NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
    raw_content_hash VARCHAR(32) NOT NULL,
    router_signature VARCHAR(64) NOT NULL,
    actual_json JSONB NOT NULL,
    predicted_json JSONB NOT NULL,
    changed BOOLEAN NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(raw_id, raw_content_hash, router_signature)
);

CREATE OR REPLACE FUNCTION rule_config_has_private_data(p_config JSONB) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE AS $fn$
    SELECT CASE jsonb_typeof(p_config)
        WHEN 'object' THEN EXISTS (
            SELECT 1 FROM jsonb_each(p_config) AS item(key,value)
            WHERE lower(item.key) = ANY(ARRAY[
                'api_key','api_hash','api_id','session','session_file','session_string',
                'phone','password','secret','token','bot_token','auth_token',
                'authorization','access_hash'
            ]) OR rule_config_has_private_data(item.value)
        )
        WHEN 'array' THEN EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_config) AS item(value)
            WHERE rule_config_has_private_data(item.value)
        )
        ELSE FALSE
    END
$fn$;

CREATE OR REPLACE FUNCTION rule_upsert_versioned(
    p_rule_name VARCHAR(128), p_rule_type VARCHAR(24), p_config JSONB,
    p_active BOOLEAN, p_config_hash VARCHAR(64), p_parser_key VARCHAR(64),
    p_template_version_id BIGINT, p_expected_version INT,
    p_actor VARCHAR(128), p_change_summary VARCHAR(512)
) RETURNS TABLE(rule_id BIGINT, version INT, rule_version_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_rule_id BIGINT;
    v_version INT;
    v_existing_version INT;
    v_snapshot_id BIGINT;
BEGIN
    IF p_rule_type NOT IN ('listing','on_duty','profile_collection','bot_collection','checkin_reverse') THEN
        RAISE EXCEPTION 'unsupported rule_type';
    END IF;
    IF rule_config_has_private_data(p_config) THEN
        RAISE EXCEPTION 'rule config contains private credentials';
    END IF;
    IF p_template_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM rule_template_versions tv JOIN rule_templates t ON t.id=tv.template_id
        WHERE tv.id=p_template_version_id AND t.rule_type=p_rule_type
    ) THEN
        RAISE EXCEPTION 'template version type mismatch';
    END IF;
    SELECT r.id, GREATEST(COALESCE(r.version, 1), 1) INTO v_rule_id, v_existing_version
    FROM ingest_rules r WHERE r.rule_name=p_rule_name FOR UPDATE;
    IF v_rule_id IS NULL THEN
        v_version := 1;
        INSERT INTO ingest_rules(rule_name,rule_type,version,config_json,is_active)
        VALUES(p_rule_name,p_rule_type,v_version,p_config,p_active)
        RETURNING id INTO v_rule_id;
    ELSE
        IF p_expected_version IS NOT NULL AND p_expected_version <> v_existing_version THEN
            RAISE EXCEPTION 'stale rule version: expected %, current %', p_expected_version, v_existing_version
                USING ERRCODE='40001';
        END IF;
        v_version := v_existing_version + 1;
        UPDATE ingest_rules SET rule_type=p_rule_type, version=v_version,
            config_json=p_config, is_active=p_active WHERE id=v_rule_id;
    END IF;
    INSERT INTO rule_instance_versions(
        rule_id,version,rule_type,config_json,config_hash,hash_algorithm,
        parser_key,template_version_id,snapshot_origin,history_complete,
        created_by,change_summary
    ) VALUES (
        v_rule_id,v_version,p_rule_type,p_config,p_config_hash,'sha256',
        p_parser_key,p_template_version_id,
        CASE WHEN v_version=1 THEN 'create' ELSE 'edit' END,TRUE,
        COALESCE(NULLIF(p_actor,''),'admin_api'),COALESCE(p_change_summary,'')
    ) RETURNING id INTO v_snapshot_id;
    UPDATE source_rule_bindings SET rule_version_id=v_snapshot_id WHERE source_rule_bindings.rule_id=v_rule_id;
    RETURN QUERY SELECT v_rule_id,v_version,v_snapshot_id;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_instance_rollback(
    p_rule_id BIGINT, p_target_version INT, p_actor VARCHAR(128), p_change_summary VARCHAR(512)
) RETURNS TABLE(rule_id BIGINT, version INT, rule_version_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_target rule_instance_versions%ROWTYPE;
    v_new_version INT;
    v_new_id BIGINT;
BEGIN
    SELECT * INTO v_target FROM rule_instance_versions
    WHERE rule_instance_versions.rule_id=p_rule_id
      AND rule_instance_versions.version=p_target_version;
    IF NOT FOUND THEN RAISE EXCEPTION 'rule version not found'; END IF;
    SELECT GREATEST(COALESCE(r.version,1),1)+1 INTO v_new_version
    FROM ingest_rules r WHERE r.id=p_rule_id FOR UPDATE;
    IF v_new_version IS NULL THEN RAISE EXCEPTION 'rule not found'; END IF;
    UPDATE ingest_rules SET version=v_new_version, rule_type=v_target.rule_type,
        config_json=v_target.config_json WHERE id=p_rule_id;
    INSERT INTO rule_instance_versions(
        rule_id,version,rule_type,config_json,config_hash,hash_algorithm,schema_version,
        parser_key,template_version_id,snapshot_origin,history_complete,created_by,change_summary
    ) VALUES (
        p_rule_id,v_new_version,v_target.rule_type,v_target.config_json,v_target.config_hash,
        v_target.hash_algorithm,v_target.schema_version,v_target.parser_key,v_target.template_version_id,
        'rollback',TRUE,COALESCE(NULLIF(p_actor,''),'admin_api'),
        COALESCE(p_change_summary,'Rollback to version '||p_target_version)
    ) RETURNING id INTO v_new_id;
    UPDATE source_rule_bindings SET rule_version_id=v_new_id WHERE source_rule_bindings.rule_id=p_rule_id;
    RETURN QUERY SELECT p_rule_id,v_new_version,v_new_id;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_template_create(
    p_name VARCHAR(128), p_rule_type VARCHAR(24), p_description TEXT,
    p_config JSONB, p_config_hash VARCHAR(64), p_parser_key VARCHAR(64),
    p_actor VARCHAR(128), p_change_summary VARCHAR(512)
) RETURNS TABLE(template_id BIGINT, version INT, template_version_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE v_template_id BIGINT; v_version_id BIGINT;
BEGIN
    IF rule_config_has_private_data(p_config) THEN
        RAISE EXCEPTION 'rule template contains private credentials';
    END IF;
    INSERT INTO rule_templates(template_name,rule_type,description,created_by)
    VALUES(p_name,p_rule_type,COALESCE(p_description,''),COALESCE(NULLIF(p_actor,''),'admin_api'))
    RETURNING id INTO v_template_id;
    INSERT INTO rule_template_versions(template_id,version,config_json,config_hash,parser_key,created_by,change_summary)
    VALUES(v_template_id,1,p_config,p_config_hash,p_parser_key,COALESCE(NULLIF(p_actor,''),'admin_api'),COALESCE(p_change_summary,''))
    RETURNING id INTO v_version_id;
    RETURN QUERY SELECT v_template_id,1,v_version_id;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_template_version_create(
    p_template_id BIGINT, p_config JSONB, p_config_hash VARCHAR(64),
    p_parser_key VARCHAR(64), p_actor VARCHAR(128), p_change_summary VARCHAR(512)
) RETURNS TABLE(template_id BIGINT, version INT, template_version_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE v_type VARCHAR(24); v_version INT; v_version_id BIGINT;
BEGIN
    IF rule_config_has_private_data(p_config) THEN
        RAISE EXCEPTION 'rule template contains private credentials';
    END IF;
    SELECT t.rule_type INTO v_type FROM rule_templates t
    WHERE t.id=p_template_id FOR UPDATE;
    IF v_type IS NULL THEN RAISE EXCEPTION 'template not found'; END IF;
    SELECT COALESCE(max(tv.version),0)+1 INTO v_version
    FROM rule_template_versions tv WHERE tv.template_id=p_template_id;
    INSERT INTO rule_template_versions(template_id,version,config_json,config_hash,parser_key,created_by,change_summary)
    VALUES(p_template_id,v_version,p_config,p_config_hash,p_parser_key,COALESCE(NULLIF(p_actor,''),'admin_api'),COALESCE(p_change_summary,''))
    RETURNING id INTO v_version_id;
    RETURN QUERY SELECT p_template_id,v_version,v_version_id;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_instance_from_template(
    p_template_version_id BIGINT, p_rule_name VARCHAR(128),
    p_actor VARCHAR(128), p_change_summary VARCHAR(512)
) RETURNS TABLE(rule_id BIGINT, version INT, rule_version_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE tv rule_template_versions%ROWTYPE; v_type VARCHAR(24); v_name VARCHAR(128); v_id BIGINT;
BEGIN
    SELECT v.* INTO tv
    FROM rule_template_versions v JOIN rule_templates t ON t.id=v.template_id
    WHERE v.id=p_template_version_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'template version not found'; END IF;
    SELECT t.rule_type,t.template_name INTO v_type,v_name
    FROM rule_template_versions v JOIN rule_templates t ON t.id=v.template_id
    WHERE v.id=p_template_version_id;
    INSERT INTO ingest_rules(rule_name,rule_type,version,config_json,is_active)
    VALUES(p_rule_name,v_type,1,tv.config_json,FALSE) RETURNING id INTO v_id;
    INSERT INTO rule_instance_versions(rule_id,version,rule_type,config_json,config_hash,hash_algorithm,
        schema_version,parser_key,template_version_id,snapshot_origin,history_complete,created_by,change_summary)
    VALUES(v_id,1,v_type,tv.config_json,tv.config_hash,tv.hash_algorithm,tv.schema_version,
        tv.parser_key,p_template_version_id,'template_instance',TRUE,
        COALESCE(NULLIF(p_actor,''),'admin_api'),COALESCE(p_change_summary,'Copied from '||v_name))
    RETURNING id INTO v_id;
    RETURN QUERY SELECT (SELECT id FROM ingest_rules WHERE rule_name=p_rule_name),1,v_id;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_copy_versioned(
    p_source_rule_id BIGINT, p_rule_name VARCHAR(128), p_actor VARCHAR(128)
) RETURNS TABLE(rule_id BIGINT, version INT, rule_version_id BIGINT)
LANGUAGE plpgsql AS $fn$
DECLARE r ingest_rules%ROWTYPE; v rule_instance_versions%ROWTYPE; v_new_rule BIGINT; v_new_version BIGINT;
        v_config JSONB;
BEGIN
    SELECT * INTO r FROM ingest_rules WHERE id=p_source_rule_id AND rule_name<>'default-rule';
    IF NOT FOUND THEN RETURN; END IF;
    IF rule_config_has_private_data(r.config_json) THEN
        RAISE EXCEPTION 'source rule contains private credentials; copy is blocked';
    END IF;
    v_config := r.config_json - ARRAY[
        'source_id','source_peer_id','peer_id','source_username','sender_peer_id',
        'sender_username','target_peer_id','target_channel_username','collection_source_id',
        'assigned_account_id'
    ];
    SELECT * INTO v FROM rule_instance_versions
    WHERE rule_instance_versions.rule_id=r.id
    ORDER BY rule_instance_versions.version DESC LIMIT 1;
    INSERT INTO ingest_rules(rule_name,rule_type,version,config_json,is_active)
    VALUES(p_rule_name,r.rule_type,1,v_config,FALSE) RETURNING id INTO v_new_rule;
    INSERT INTO rule_instance_versions(rule_id,version,rule_type,config_json,config_hash,hash_algorithm,
        schema_version,parser_key,template_version_id,snapshot_origin,history_complete,created_by,change_summary)
    VALUES(v_new_rule,1,r.rule_type,v_config,md5(v_config::text),
        'md5',COALESCE(v.schema_version,1),COALESCE(v.parser_key,'ast_rules_v1'),
        v.template_version_id,'copy',TRUE,COALESCE(NULLIF(p_actor,''),'admin_api'),'Copied rule instance')
    RETURNING id INTO v_new_version;
    RETURN QUERY SELECT v_new_rule,1,v_new_version;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_bind_source_versioned(
    p_rule_id BIGINT, p_source_id BIGINT, p_priority INT, p_purpose VARCHAR(64),
    p_match_group VARCHAR(64), p_match_mode VARCHAR(32), p_fallback_order INT,
    p_rule_version_id BIGINT
) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
DECLARE v_version_id BIGINT; v_purpose VARCHAR(64); v_rule_type VARCHAR(24);
BEGIN
    SELECT r.rule_type, COALESCE(p_rule_version_id,
        (SELECT v.id FROM rule_instance_versions v WHERE v.rule_id=r.id ORDER BY v.version DESC LIMIT 1))
    INTO v_rule_type,v_version_id FROM ingest_rules r WHERE r.id=p_rule_id;
    IF v_rule_type IS NULL OR NOT EXISTS (SELECT 1 FROM ingest_sources WHERE id=p_source_id) THEN
        RAISE EXCEPTION 'rule or source not found';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM rule_instance_versions WHERE id=v_version_id AND rule_id=p_rule_id) THEN
        RAISE EXCEPTION 'rule version does not belong to rule';
    END IF;
    v_purpose := COALESCE(NULLIF(p_purpose,''),v_rule_type);
    INSERT INTO source_rule_bindings(source_id,rule_id,priority,rule_version_id)
    VALUES(p_source_id,p_rule_id,p_priority,v_version_id)
    ON CONFLICT(source_id,rule_id) DO UPDATE SET priority=EXCLUDED.priority,
        rule_version_id=EXCLUDED.rule_version_id;
    INSERT INTO source_rule_binding_policies(source_id,rule_id,purpose,match_group,match_mode,fallback_order,enabled)
    VALUES(p_source_id,p_rule_id,v_purpose,COALESCE(NULLIF(p_match_group,''),'default'),
        COALESCE(NULLIF(p_match_mode,''),'legacy_single_selected'),COALESCE(p_fallback_order,0),TRUE)
    ON CONFLICT(source_id,rule_id,purpose) DO UPDATE SET
        match_group=EXCLUDED.match_group,match_mode=EXCLUDED.match_mode,
        fallback_order=EXCLUDED.fallback_order,enabled=TRUE;
    RETURN TRUE;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_router_mode_set(p_source_id BIGINT,p_mode VARCHAR(12)) RETURNS BOOLEAN
LANGUAGE plpgsql AS $fn$
BEGIN
    IF p_mode NOT IN ('legacy','shadow') THEN RAISE EXCEPTION '2A permits legacy or shadow only'; END IF;
    UPDATE ingest_sources SET router_mode=p_mode WHERE id=p_source_id;
    RETURN FOUND;
END
$fn$;

CREATE OR REPLACE FUNCTION rule_shadow_bindings_for_source(p_source_id BIGINT)
RETURNS TABLE(rule_id BIGINT,rule_type VARCHAR(24),is_active BOOLEAN,is_default BOOLEAN,
    priority INT,rule_version_id BIGINT,config_json JSONB,purpose VARCHAR(64),match_group VARCHAR(64),
    mode VARCHAR(32),fallback_order INT,config_hash VARCHAR(64))
LANGUAGE sql STABLE AS $fn$
    SELECT r.id,r.rule_type,r.is_active,FALSE,b.priority,b.rule_version_id,
           COALESCE(v.config_json,r.config_json),COALESCE(p.purpose,r.rule_type),
           COALESCE(p.match_group,'default'),COALESCE(p.match_mode,'legacy_single_selected'),
           COALESCE(p.fallback_order,0),COALESCE(v.config_hash,md5(r.config_json::text))
    FROM source_rule_bindings b JOIN ingest_rules r ON r.id=b.rule_id
    LEFT JOIN rule_instance_versions v ON v.id=b.rule_version_id
    LEFT JOIN source_rule_binding_policies p ON p.source_id=b.source_id AND p.rule_id=b.rule_id AND p.enabled
    WHERE b.source_id=p_source_id AND r.is_active
    UNION ALL
    SELECT r.id,r.rule_type,r.is_active,TRUE,(-2147483647)::int,v.id,
           COALESCE(v.config_json,r.config_json),'listing','default','legacy_single_selected',0,
           COALESCE(v.config_hash,md5(r.config_json::text))
    FROM ingest_rules r LEFT JOIN rule_instance_versions v
      ON v.rule_id=r.id AND v.version=GREATEST(COALESCE(r.version,1),1)
    WHERE r.rule_name='default-rule' AND r.is_active
$fn$;

CREATE OR REPLACE FUNCTION extract_claim_batch(p_limit INT)
RETURNS TABLE(raw_id BIGINT,source_id BIGINT,platform_msg_id BIGINT,sender_peer_id BIGINT,
    grouped_id BIGINT,raw_text TEXT,media_json JSONB,posted_at TIMESTAMPTZ,rule_config JSONB)
LANGUAGE plpgsql AS $fn$
BEGIN
    RETURN QUERY
    WITH claimed AS (
        SELECT r.id FROM raw_messages r
        WHERE r.extract_state='new' OR (r.extract_state='processing'
            AND r.extract_claimed_at<NOW()-INTERVAL '10 minutes')
        ORDER BY r.id LIMIT GREATEST(1,p_limit) FOR UPDATE OF r SKIP LOCKED
    )
    UPDATE raw_messages rm
    SET extract_state='processing',extract_claimed_at=NOW(),
        rule_version_id=COALESCE(rm.rule_version_id,
            (SELECT COALESCE(b.rule_version_id, v.id) FROM source_rule_bindings b JOIN ingest_rules ir ON ir.id=b.rule_id
             LEFT JOIN rule_instance_versions v ON v.rule_id=ir.id
                AND v.version=GREATEST(COALESCE(ir.version,1),1)
             WHERE b.source_id=rm.source_id AND ir.is_active AND ir.rule_type='listing'
             ORDER BY b.priority DESC,ir.id LIMIT 1),
            (SELECT v.id FROM ingest_rules ir JOIN rule_instance_versions v
             ON v.rule_id=ir.id AND v.version=GREATEST(COALESCE(ir.version,1),1)
             WHERE ir.rule_name='default-rule' AND ir.is_active LIMIT 1))
    FROM claimed c WHERE rm.id=c.id
    RETURNING rm.id,rm.source_id,rm.platform_msg_id,rm.sender_peer_id,rm.grouped_id,
        rm.raw_text,rm.media_json,rm.posted_at,
        COALESCE((SELECT v.config_json FROM rule_instance_versions v WHERE v.id=rm.rule_version_id),
            (SELECT ir.config_json FROM source_rule_bindings b JOIN ingest_rules ir ON ir.id=b.rule_id
             WHERE b.source_id=rm.source_id AND ir.is_active AND ir.rule_type='listing'
             ORDER BY b.priority DESC,ir.id LIMIT 1),
            (SELECT ir.config_json FROM ingest_rules ir WHERE ir.rule_name='default-rule' AND ir.is_active LIMIT 1));
END
$fn$;

-- Initialize revision history for future raw insert/edit events; existing
-- historical rows intentionally remain without a fabricated edit history.
DROP TRIGGER IF EXISTS trg_raw_message_revision ON raw_messages;
CREATE TRIGGER trg_raw_message_revision
AFTER INSERT OR UPDATE OF raw_text, media_json, posted_at ON raw_messages
FOR EACH ROW EXECUTE FUNCTION capture_raw_message_revision();
COMMIT;
