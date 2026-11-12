UPDATE listing_media_ocr
SET status = $2,
    classification = CASE WHEN human_classification IS NOT NULL AND reviewed_at IS NOT NULL THEN human_classification ELSE $3 END,
    review_status = CASE WHEN human_classification IS NOT NULL AND reviewed_at IS NOT NULL
                         THEN CASE WHEN human_classification = 'info_card' THEN 'confirmed' ELSE 'not_card' END ELSE $4 END,
    raw_text = $5,
    normalized_text = CASE WHEN human_classification IS NOT NULL AND reviewed_at IS NOT NULL THEN normalized_text ELSE $6 END,
    fields_json = CASE WHEN human_classification IS NOT NULL AND reviewed_at IS NOT NULL THEN fields_json ELSE $7::jsonb END,
    evidence_json = $8::jsonb,
    confidence = $9,
    engine_version = $10,
    error_text = $11,
    predicted_classification = $12,
    predicted_confidence = $13,
    classifier_version_id = $14,
    predicted_model_version = NULL,
    predicted_model_sha = NULL,
    predicted_score_semantics = NULL,
    classifier_signals_json = $15::jsonb,
    updated_at = NOW()
WHERE id = $1
RETURNING classification, review_status;
