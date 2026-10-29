UPDATE listing_media_ocr
SET status = $2,
    classification = $3,
    review_status = $4,
    raw_text = $5,
    normalized_text = $6,
    fields_json = $7::jsonb,
    evidence_json = $8::jsonb,
    confidence = $9,
    engine_version = $10,
    error_text = $11,
    updated_at = NOW()
WHERE id = $1;
