BEGIN;

ALTER TABLE site_users
    ADD COLUMN IF NOT EXISTS chat_alias VARCHAR(32)
        DEFAULT ('访客-' || substr(md5(random()::text || clock_timestamp()::text), 1, 10)),
    ADD COLUMN IF NOT EXISTS chat_alias_is_custom BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS chat_alias_changed_at TIMESTAMPTZ;
UPDATE site_users
SET chat_alias = '访客-' || substr(md5(random()::text || clock_timestamp()::text || id::text), 1, 10)
WHERE chat_alias IS NULL OR btrim(chat_alias) = '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_site_users_chat_alias_normalized
    ON site_users (lower(regexp_replace(btrim(chat_alias), '\s+', '', 'g')));

CREATE TABLE IF NOT EXISTS chat_alias_audit (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES site_users(id) ON DELETE CASCADE,
    old_alias VARCHAR(32) NOT NULL,
    new_alias VARCHAR(32) NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_initial BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE OR REPLACE FUNCTION chat_alias_update(p_user_id BIGINT, p_alias VARCHAR(32))
RETURNS TABLE(outcome TEXT, current_alias VARCHAR(32), changed_at TIMESTAMPTZ,
              next_change_at TIMESTAMPTZ)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_old VARCHAR(32); v_custom BOOLEAN; v_changed TIMESTAMPTZ;
    v_alias VARCHAR(32) := btrim(p_alias);
BEGIN
    IF char_length(v_alias) < 2 OR char_length(v_alias) > 24
       OR v_alias !~ '^[[:alnum:]_ -]+$'
       OR lower(v_alias) ~ '^(admin|administrator|system|nexus|loom|客服|管理员|官方|系统)([ _-].*)?$' THEN
        RETURN QUERY SELECT 'INVALID', NULL::VARCHAR(32), NULL::TIMESTAMPTZ, NULL::TIMESTAMPTZ;
        RETURN;
    END IF;
    SELECT su.chat_alias, su.chat_alias_is_custom, su.chat_alias_changed_at
    INTO v_old, v_custom, v_changed
    FROM site_users su WHERE su.id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'NOT_FOUND', NULL::VARCHAR(32), NULL::TIMESTAMPTZ, NULL::TIMESTAMPTZ;
        RETURN;
    END IF;
    IF regexp_replace(lower(v_alias), '\s+', '', 'g')
       = regexp_replace(lower(v_old), '\s+', '', 'g') THEN
        RETURN QUERY SELECT 'UNCHANGED', v_old, v_changed,
            CASE WHEN v_custom AND v_changed IS NOT NULL THEN v_changed + INTERVAL '6 months' END;
        RETURN;
    END IF;
    IF v_custom AND v_changed IS NOT NULL AND NOW() < v_changed + INTERVAL '6 months' THEN
        RETURN QUERY SELECT 'TOO_SOON', v_old, v_changed, v_changed + INTERVAL '6 months';
        RETURN;
    END IF;
    UPDATE site_users
    SET chat_alias = v_alias,
        chat_alias_is_custom = TRUE,
        chat_alias_changed_at = NOW()
    WHERE id = p_user_id;
    INSERT INTO chat_alias_audit(user_id, old_alias, new_alias, is_initial)
    VALUES (p_user_id, v_old, v_alias, NOT v_custom);
    RETURN QUERY SELECT 'UPDATED', v_alias, NOW(), NOW() + INTERVAL '6 months';
EXCEPTION WHEN unique_violation THEN
    RETURN QUERY SELECT 'TAKEN', NULL::VARCHAR(32), NULL::TIMESTAMPTZ, NULL::TIMESTAMPTZ;
END
$fn$;

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS outbound_alias_snapshot VARCHAR(32);
UPDATE chat_messages m SET outbound_alias_snapshot = su.chat_alias
FROM chat_sessions cs JOIN site_users su ON su.id = cs.user_id
WHERE m.session_id = cs.id AND m.direction = 'out'
  AND m.outbound_alias_snapshot IS NULL;

ALTER TABLE bot_collection_results
    ADD COLUMN IF NOT EXISTS verification_status VARCHAR(24) NOT NULL DEFAULT 'unverified',
    ADD COLUMN IF NOT EXISTS verification_reason TEXT,
    ADD COLUMN IF NOT EXISTS verified_by BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
ALTER TABLE bot_collection_results DROP CONSTRAINT IF EXISTS bot_collection_results_verification_status_check;
ALTER TABLE bot_collection_results ADD CONSTRAINT bot_collection_results_verification_status_check
    CHECK (verification_status IN ('unverified','verified','unrelated','unavailable'));
CREATE TABLE IF NOT EXISTS bot_collection_audit (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES bot_collection_results(job_id) ON DELETE CASCADE,
    reviewer_id BIGINT NOT NULL REFERENCES site_users(id),
    old_status VARCHAR(24) NOT NULL,
    new_status VARCHAR(24) NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

UPDATE profile_collection_jobs
SET stage = CASE WHEN status = 'skipped' THEN 'checked' ELSE stage END,
    progress_note = CASE WHEN status = 'skipped' AND COALESCE(progress_note,'') = ''
                         THEN '已有资料，正常跳过' ELSE progress_note END,
    updated_at = NOW()
WHERE status = 'skipped' AND stage = 'failed';

-- A normalized name alone is a similarity hint, not a duplicate identity.
UPDATE listings l
SET status = 'pending', updated_at = NOW()
WHERE l.status = 'duplicate_pending'
  AND (l.profile_collection_job_id IS NOT NULL OR EXISTS (
      SELECT 1 FROM raw_messages r JOIN checkin_history_scans h ON h.source_id=r.source_id
      WHERE r.id=l.source_msg_id
  ))
  AND NOT EXISTS (
      SELECT 1 FROM listings other
      WHERE other.id<>l.id
        AND other.status IN ('pending','duplicate_pending','published','archived')
        AND ((l.contact_peer_id IS NOT NULL AND l.contact_peer_id=other.contact_peer_id)
          OR (NULLIF(l.contact_username,'') IS NOT NULL
              AND lower(l.contact_username)=lower(other.contact_username)
              AND (l.contact_peer_id IS NULL OR other.contact_peer_id IS NULL
                   OR l.contact_peer_id=other.contact_peer_id)))
  );

-- Preserve all candidate rows while making the check-in review reason explicit.
UPDATE listings l
SET evidence_json = COALESCE(l.evidence_json,'{}'::jsonb) || jsonb_build_object(
    'review_class', CASE
      WHEN EXISTS (
        SELECT 1 FROM listings other
        WHERE other.id<>l.id AND other.status IN ('pending','duplicate_pending','published','archived')
          AND ((l.contact_peer_id IS NOT NULL AND l.contact_peer_id=other.contact_peer_id)
            OR (NULLIF(l.contact_username,'') IS NOT NULL
                AND lower(l.contact_username)=lower(other.contact_username)
                AND (l.contact_peer_id IS NULL OR other.contact_peer_id IS NULL
                     OR l.contact_peer_id=other.contact_peer_id)))
      ) THEN 'identity_link_candidate'
      WHEN EXISTS (
        SELECT 1 FROM listings other
        WHERE other.id<>l.id AND other.status IN ('pending','duplicate_pending','published','archived')
          AND NULLIF(l.nickname,'') IS NOT NULL AND NULLIF(other.nickname,'') IS NOT NULL
          AND regexp_replace(lower(l.nickname),'[[:space:][:punct:]_]+','','g')
              =regexp_replace(lower(other.nickname),'[[:space:][:punct:]_]+','','g')
      ) THEN 'suspected_duplicate'
      WHEN (NULLIF(l.contact_username,'') IS NOT NULL OR l.contact_peer_id IS NOT NULL)
       AND (NULLIF(l.nickname,'') IS NOT NULL OR NULLIF(l.district,'') IS NOT NULL
            OR l.price_amount IS NOT NULL OR NULLIF(l.description,'') IS NOT NULL
            OR jsonb_array_length(COALESCE(l.media_json,'[]'::jsonb))>0)
        THEN 'high_value_review'
      WHEN NULLIF(l.description,'') IS NULL AND jsonb_array_length(COALESCE(l.media_json,'[]'::jsonb))=0
       AND NULLIF(l.nickname,'') IS NULL AND NULLIF(l.district,'') IS NULL AND l.price_amount IS NULL
        THEN 'low_value_staged'
      ELSE 'missing_information'
    END
), updated_at=NOW()
WHERE l.profile_collection_job_id IS NOT NULL OR EXISTS (
    SELECT 1 FROM raw_messages r JOIN checkin_history_scans h ON h.source_id=r.source_id
    WHERE r.id=l.source_msg_id
);

CREATE TABLE IF NOT EXISTS chat_deletion_audit (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL REFERENCES site_users(id) ON DELETE CASCADE,
    deleted_message_count INT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION chat_delete_session(p_session_id BIGINT, p_user_id BIGINT)
RETURNS TABLE(outcome TEXT, media_paths TEXT[])
LANGUAGE plpgsql AS $fn$
DECLARE
    v_owner BIGINT; v_paths TEXT[]; v_count INT;
BEGIN
    SELECT cs.user_id INTO v_owner
    FROM chat_sessions cs WHERE cs.id = p_session_id FOR UPDATE;
    IF v_owner IS NULL OR v_owner <> p_user_id THEN
        RETURN QUERY SELECT 'NOT_FOUND', ARRAY[]::TEXT[];
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM outbox_jobs oj JOIN chat_messages cm ON cm.id = oj.chat_msg_id
        WHERE cm.session_id = p_session_id AND oj.status = 'processing'
    ) THEN
        RETURN QUERY SELECT 'IN_FLIGHT', ARRAY[]::TEXT[];
        RETURN;
    END IF;
    SELECT COALESCE(array_agg(DISTINCT media_path) FILTER (WHERE media_path IS NOT NULL), ARRAY[]::TEXT[]),
           count(*)::INT
    INTO v_paths, v_count FROM chat_messages WHERE session_id = p_session_id;
    INSERT INTO chat_deletion_audit(session_id,user_id,deleted_message_count)
    VALUES(p_session_id,p_user_id,v_count);
    UPDATE outbox_jobs oj SET status='cancelled', last_error='cancelled by owner'
    FROM chat_messages cm
    WHERE cm.session_id=p_session_id AND oj.chat_msg_id=cm.id AND oj.status='pending';
    DELETE FROM chat_sessions WHERE id=p_session_id AND user_id=p_user_id;
    RETURN QUERY SELECT 'DELETED', v_paths;
END
$fn$;

DROP FUNCTION IF EXISTS chat_enqueue_out(BIGINT, BIGINT, TEXT, TEXT, VARCHAR, TEXT);
CREATE FUNCTION chat_enqueue_out(
    p_session_id BIGINT, p_user_id BIGINT, p_client_msg_id TEXT, p_content TEXT,
    p_media_kind VARCHAR(16), p_media_b64 TEXT
) RETURNS TABLE(message_id BIGINT, job_id BIGINT, status TEXT)
LANGUAGE plpgsql AS $fn$
DECLARE v_owner BIGINT; v_state TEXT; v_alias VARCHAR(32); v_msg BIGINT; v_job BIGINT; v_msg_status TEXT;
BEGIN
    SELECT cs.user_id, cs.status, su.chat_alias INTO v_owner, v_state, v_alias
    FROM chat_sessions cs JOIN site_users su ON su.id=cs.user_id
    WHERE cs.id=p_session_id FOR UPDATE OF cs;
    IF v_owner IS NULL THEN RETURN QUERY SELECT NULL::BIGINT,NULL::BIGINT,'NOT_FOUND'; RETURN; END IF;
    IF v_owner <> p_user_id THEN RETURN QUERY SELECT NULL::BIGINT,NULL::BIGINT,'FORBIDDEN'; RETURN; END IF;
    IF v_state <> 'open' THEN RETURN QUERY SELECT NULL::BIGINT,NULL::BIGINT,'CLOSED'; RETURN; END IF;
    IF COALESCE(p_content,'')='' AND p_media_b64 IS NULL THEN
        RETURN QUERY SELECT NULL::BIGINT,NULL::BIGINT,'EMPTY'; RETURN;
    END IF;
    IF p_client_msg_id IS NOT NULL THEN
        SELECT m.id,oj.id,m.status INTO v_msg,v_job,v_msg_status
        FROM chat_messages m LEFT JOIN outbox_jobs oj ON oj.chat_msg_id=m.id
        WHERE m.session_id=p_session_id AND m.direction='out' AND m.client_msg_id=p_client_msg_id
        LIMIT 1;
        IF v_msg IS NOT NULL THEN
            RETURN QUERY SELECT v_msg,v_job,v_msg_status;
            RETURN;
        END IF;
    END IF;
    INSERT INTO chat_messages(session_id,client_msg_id,direction,content,status,media_kind,media_b64,outbound_alias_snapshot)
    VALUES(p_session_id,p_client_msg_id,'out',COALESCE(p_content,''),'queued',p_media_kind,p_media_b64,v_alias)
    RETURNING id INTO v_msg;
    INSERT INTO outbox_jobs(chat_msg_id) VALUES(v_msg) RETURNING id INTO v_job;
    RETURN QUERY SELECT v_msg,v_job,'queued';
END
$fn$;

DROP FUNCTION IF EXISTS outbox_claim_job(BIGINT);
CREATE FUNCTION outbox_claim_job(p_account_id BIGINT)
RETURNS TABLE(job_id BIGINT, chat_msg_id BIGINT, session_id BIGINT,
              contact_peer_id BIGINT, contact_username VARCHAR(128), client_msg_id VARCHAR(64),
              content TEXT, display_name VARCHAR(128), is_first BOOLEAN, has_inbound BOOLEAN,
              limit_mode VARCHAR(16), delivery_channel VARCHAR(16), fallback_bot VARCHAR(128),
              media_kind VARCHAR(16), media_b64 TEXT)
LANGUAGE plpgsql AS $fn$
DECLARE v_job BIGINT;
BEGIN
    WITH due AS (
        SELECT oj.id FROM outbox_jobs oj
        JOIN chat_messages cm ON cm.id=oj.chat_msg_id
        JOIN chat_sessions cs ON cs.id=cm.session_id
        WHERE cs.assigned_account_id=p_account_id AND cs.status='open'
          AND ((oj.status='pending' AND oj.run_after<=NOW())
            OR (oj.status='processing' AND oj.run_after<NOW()-INTERVAL '10 minutes'))
        ORDER BY oj.id LIMIT 1 FOR UPDATE OF oj,cs SKIP LOCKED
    )
    UPDATE outbox_jobs o SET status='processing',run_after=NOW()
    FROM due WHERE o.id=due.id RETURNING o.id INTO v_job;
    IF v_job IS NULL THEN RETURN; END IF;
    RETURN QUERY SELECT oj.id,oj.chat_msg_id,cs.id,cs.contact_peer_id,cs.contact_username,
        cm.client_msg_id,cm.content,cm.outbound_alias_snapshot,
        NOT EXISTS(SELECT 1 FROM chat_messages m2 WHERE m2.session_id=cs.id AND m2.direction='out' AND m2.id<>cm.id),
        EXISTS(SELECT 1 FROM chat_messages m3 WHERE m3.session_id=cs.id AND m3.direction='in'),
        (SELECT gl.mode FROM gateway_limits gl WHERE gl.account_id=p_account_id AND gl.day=CURRENT_DATE),
        cs.delivery_channel,cs.fallback_bot,cm.media_kind,cm.media_b64
    FROM outbox_jobs oj JOIN chat_messages cm ON cm.id=oj.chat_msg_id
    JOIN chat_sessions cs ON cs.id=cm.session_id WHERE oj.id=v_job;
END
$fn$;

COMMIT;
