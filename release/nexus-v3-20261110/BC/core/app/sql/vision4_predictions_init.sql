CREATE TABLE IF NOT EXISTS media_vision_predictions (
    ocr_id BIGINT NOT NULL REFERENCES listing_media_ocr(id) ON DELETE CASCADE,
    model_key VARCHAR(220) NOT NULL,
    model_version VARCHAR(128) NOT NULL,
    model_sha VARCHAR(64),
    run_mode VARCHAR(8) NOT NULL CHECK (run_mode IN ('shadow', 'active')),
    status VARCHAR(16) NOT NULL CHECK (status IN ('classified', 'error', 'unavailable')),
    category VARCHAR(32),
    category_zh VARCHAR(48),
    scores_json JSONB NOT NULL DEFAULT '{}',
    score_semantics VARCHAR(64),
    model_load_ms DOUBLE PRECISION,
    inference_ms DOUBLE PRECISION,
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ocr_id, model_key)
);

CREATE INDEX IF NOT EXISTS idx_media_vision_predictions_version
    ON media_vision_predictions(model_version, status, category);

ALTER TABLE media_vision_predictions
    ADD COLUMN IF NOT EXISTS mapped_classification VARCHAR(48);

ALTER TABLE listing_media_ocr
    ADD COLUMN IF NOT EXISTS predicted_model_version VARCHAR(128),
    ADD COLUMN IF NOT EXISTS predicted_model_sha VARCHAR(64),
    ADD COLUMN IF NOT EXISTS predicted_score_semantics VARCHAR(64);

CREATE TABLE IF NOT EXISTS media_vision_asset_predictions (
    media_path VARCHAR(512) NOT NULL,
    model_key VARCHAR(220) NOT NULL,
    ocr_id BIGINT REFERENCES listing_media_ocr(id) ON DELETE SET NULL,
    model_version VARCHAR(128) NOT NULL,
    model_sha VARCHAR(64),
    run_mode VARCHAR(8) NOT NULL CHECK (run_mode IN ('shadow', 'active')),
    status VARCHAR(16) NOT NULL CHECK (status IN ('classified', 'error', 'unavailable')),
    category VARCHAR(32),
    mapped_classification VARCHAR(48),
    category_zh VARCHAR(48),
    scores_json JSONB NOT NULL DEFAULT '{}',
    score_semantics VARCHAR(64),
    model_load_ms DOUBLE PRECISION,
    inference_ms DOUBLE PRECISION,
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (media_path, model_key)
);

CREATE INDEX IF NOT EXISTS idx_media_vision_asset_predictions_version
    ON media_vision_asset_predictions(model_version, status, mapped_classification);
