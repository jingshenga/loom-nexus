WITH target AS MATERIALIZED (
    SELECT id, source_msg_id, manual_fields
    FROM listings
    WHERE id = $5::bigint AND source_msg_id = $1::bigint
    FOR UPDATE
), refreshed AS (
    UPDATE raw_messages rm
    SET raw_text = $2::text,
        posted_at = COALESCE($3::timestamptz, rm.posted_at),
        media_json = CASE
            WHEN t.manual_fields ? 'media' THEN rm.media_json
            WHEN $4::jsonb IS NOT NULL AND jsonb_array_length($4::jsonb) > 0
            THEN $4::jsonb ELSE rm.media_json END,
        extract_state = 'new',
        extract_claimed_at = NULL
    FROM target t
    WHERE rm.id = $1::bigint
    RETURNING rm.id, rm.media_json
), card_media AS (
    UPDATE listings l
    SET media_json = r.media_json, updated_at = NOW()
    FROM refreshed r
    WHERE l.id = $5::bigint
      AND jsonb_array_length(r.media_json) > 0
    RETURNING l.id
)
SELECT r.id AS raw_id FROM refreshed r;
