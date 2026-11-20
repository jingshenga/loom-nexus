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
    predicted_classification = $12,
    predicted_confidence = $13,
    classifier_version_id = $14,
    classifier_signals_json = $15::jsonb,
    file_sha256 = COALESCE($16, file_sha256),
    updated_at = NOW()
WHERE id = $1;
