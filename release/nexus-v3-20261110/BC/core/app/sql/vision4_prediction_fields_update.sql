UPDATE listing_media_ocr
SET predicted_classification = $2,
    predicted_confidence = $3,
    classifier_version_id = NULL,
    predicted_model_version = $4,
    predicted_model_sha = $5,
    predicted_score_semantics = $6
WHERE media_path = $1;
