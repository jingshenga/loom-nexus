ALTER TABLE listings ADD COLUMN IF NOT EXISTS profile_collection_job_id BIGINT
    REFERENCES profile_collection_jobs(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_profile_collection_job
    ON listings(profile_collection_job_id)
    WHERE profile_collection_job_id IS NOT NULL;

INSERT INTO listings (
    profile_collection_job_id, contact_peer_id, contact_username, nickname,
    description, media_json, evidence_json, status, confidence
)
SELECT j.id, r.contact_peer_id, NULLIF(r.contact_username, ''),
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
FROM profile_collection_results r
JOIN profile_collection_jobs j ON j.id = r.job_id
WHERE j.status = 'fetched'
ON CONFLICT (profile_collection_job_id) WHERE profile_collection_job_id IS NOT NULL
DO NOTHING;

UPDATE listing_media_ocr o
SET listing_id = l.id
FROM profile_collection_results r
JOIN listings l ON l.profile_collection_job_id = r.job_id
WHERE o.profile_result_id = r.id
  AND o.listing_id IS DISTINCT FROM l.id;

UPDATE listings l
SET media_json = COALESCE((
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
), '[]'::jsonb)
FROM profile_collection_results r
WHERE l.profile_collection_job_id = r.job_id;
