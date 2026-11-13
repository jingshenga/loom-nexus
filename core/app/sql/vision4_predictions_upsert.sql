INSERT INTO media_vision_predictions (
    ocr_id, model_key, model_version, model_sha, run_mode, status,
    category, mapped_classification, category_zh, scores_json, score_semantics,
    model_load_ms, inference_ms, error_code
)
VALUES (
    $1, $2, $3, $4, $5, $6,
    $7, $8, $9, $10::jsonb, $11,
    $12, $13, $14
)
ON CONFLICT (ocr_id, model_key) DO UPDATE SET
    model_sha = EXCLUDED.model_sha,
    run_mode = EXCLUDED.run_mode,
    status = EXCLUDED.status,
    category = EXCLUDED.category,
    mapped_classification = EXCLUDED.mapped_classification,
    category_zh = EXCLUDED.category_zh,
    scores_json = EXCLUDED.scores_json,
    score_semantics = EXCLUDED.score_semantics,
    model_load_ms = EXCLUDED.model_load_ms,
    inference_ms = EXCLUDED.inference_ms,
    error_code = EXCLUDED.error_code,
    updated_at = NOW();
