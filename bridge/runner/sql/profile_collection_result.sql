WITH result AS (
    INSERT INTO profile_collection_results (
        job_id, contact_peer_id, contact_username, profile_url, channel_url,
        bot_url, description, media_json, evidence_json, profile_config
    ) VALUES (
        $1::bigint, $2::bigint, $3::text, $4::text, $5::text,
        $6::text, $7::text, $8::jsonb, $9::jsonb, $10::jsonb
    )
    ON CONFLICT (job_id) DO UPDATE SET
        contact_peer_id = EXCLUDED.contact_peer_id,
        contact_username = EXCLUDED.contact_username,
        profile_url = EXCLUDED.profile_url,
        channel_url = EXCLUDED.channel_url,
        bot_url = EXCLUDED.bot_url,
        description = EXCLUDED.description,
        media_json = EXCLUDED.media_json,
        evidence_json = EXCLUDED.evidence_json,
        profile_config = EXCLUDED.profile_config,
        collected_at = NOW()
    RETURNING id, job_id, contact_peer_id, contact_username,
              description, media_json, evidence_json
), promoted AS (
    INSERT INTO listings (
        profile_collection_job_id, contact_peer_id, contact_username, nickname,
        description, media_json, evidence_json, status, confidence
    )
    SELECT r.job_id, r.contact_peer_id, NULLIF(r.contact_username, ''),
           CASE WHEN NULLIF(r.contact_username, '') IS NOT NULL
                THEN '@' || r.contact_username
                ELSE 'TG ID ' || r.contact_peer_id::text END,
           NULLIF(r.description, ''),
           COALESCE((
               SELECT jsonb_agg(
                   CASE
                       WHEN item->>'kind' <> 'photo' OR item ? 'visibility' THEN item
                       WHEN item->>'ocr_classification' IN ('info_card', 'uncertain')
                           THEN item || jsonb_build_object('visibility', 'admin')
                       WHEN item->>'ocr_status' = 'done'
                           THEN item || jsonb_build_object('visibility', 'public')
                       ELSE item || jsonb_build_object('visibility', 'pending')
                   END
                   ORDER BY ord
               )
               FROM jsonb_array_elements(r.media_json) WITH ORDINALITY AS a(item, ord)
           ), '[]'::jsonb),
           r.evidence_json, 'pending', 0.5
    FROM result r
    ON CONFLICT (profile_collection_job_id) WHERE profile_collection_job_id IS NOT NULL
    DO UPDATE SET
        contact_peer_id = EXCLUDED.contact_peer_id,
        contact_username = EXCLUDED.contact_username,
        nickname = EXCLUDED.nickname,
        description = EXCLUDED.description,
        media_json = EXCLUDED.media_json,
        evidence_json = EXCLUDED.evidence_json,
        updated_at = NOW()
    WHERE listings.status = 'pending' AND listings.manual_fields = '{}'::jsonb
    RETURNING id
)
SELECT id FROM result;
