BEGIN;

CREATE TABLE IF NOT EXISTS ocr_classification_categories (
    code VARCHAR(48) PRIMARY KEY,
    name VARCHAR(80) NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_trainable BOOLEAN NOT NULL DEFAULT TRUE,
    review_required BOOLEAN NOT NULL DEFAULT FALSE,
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    created_by BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO ocr_classification_categories
    (code, name, is_active, is_trainable, review_required, is_builtin)
VALUES
    ('info_card', '资料卡', TRUE, TRUE, TRUE, TRUE),
    ('profile_photo', '人像', TRUE, TRUE, FALSE, TRUE),
    ('advertisement', '广告', TRUE, TRUE, FALSE, TRUE),
    ('other', '其他', TRUE, TRUE, FALSE, TRUE),
    ('uncertain', '不确定', TRUE, FALSE, TRUE, TRUE)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS ocr_classifier_versions (
    id BIGSERIAL PRIMARY KEY,
    status VARCHAR(16) NOT NULL DEFAULT 'tested'
        CHECK (status IN ('tested', 'ready', 'active', 'retired')),
    parent_version_id BIGINT REFERENCES ocr_classifier_versions(id) ON DELETE SET NULL,
    model_json JSONB NOT NULL,
    policy_json JSONB NOT NULL DEFAULT '{}',
    metrics_json JSONB NOT NULL DEFAULT '{}',
    created_by BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prepared_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ocr_classifier_versions_status
    ON ocr_classifier_versions(status, id DESC);

ALTER TABLE listing_media_ocr
    ADD COLUMN IF NOT EXISTS human_classification VARCHAR(48),
    ADD COLUMN IF NOT EXISTS predicted_classification VARCHAR(48),
    ADD COLUMN IF NOT EXISTS predicted_confidence NUMERIC(6,5),
    ADD COLUMN IF NOT EXISTS classifier_signals_json JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS classifier_version_id BIGINT
        REFERENCES ocr_classifier_versions(id) ON DELETE SET NULL;
UPDATE listing_media_ocr
SET predicted_classification=classification,
    predicted_confidence=confidence
WHERE predicted_classification IS NULL;

CREATE TABLE IF NOT EXISTS ocr_classification_labels (
    id BIGSERIAL PRIMARY KEY,
    ocr_id BIGINT NOT NULL REFERENCES listing_media_ocr(id) ON DELETE CASCADE,
    category_code VARCHAR(48) NOT NULL REFERENCES ocr_classification_categories(code),
    label_source VARCHAR(24) NOT NULL DEFAULT 'manual'
        CHECK (label_source IN ('manual', 'legacy_binary')),
    labeled_by BIGINT REFERENCES site_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ocr_classification_labels_current
    ON ocr_classification_labels(ocr_id, created_at DESC)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ocr_classification_labels_category
    ON ocr_classification_labels(category_code, created_at DESC)
    WHERE superseded_at IS NULL;

CREATE TABLE IF NOT EXISTS ocr_classifier_shadow_predictions (
    version_id BIGINT NOT NULL REFERENCES ocr_classifier_versions(id) ON DELETE CASCADE,
    ocr_id BIGINT NOT NULL REFERENCES listing_media_ocr(id) ON DELETE CASCADE,
    predicted_class VARCHAR(48) NOT NULL,
    confidence NUMERIC(6,5) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (version_id, ocr_id)
);

-- Preserve prior human decisions as training labels. The old non-card decision
-- carried no subtype, so it is mapped to the broad "other" category and remains
-- editable in the OCR review UI before a model is published.
INSERT INTO ocr_classification_labels (ocr_id, category_code, label_source)
SELECT id, CASE WHEN review_status = 'confirmed' THEN 'info_card' ELSE 'other' END,
       'legacy_binary'
FROM listing_media_ocr
WHERE review_status IN ('confirmed', 'not_card')
  AND NOT EXISTS (
      SELECT 1 FROM ocr_classification_labels label
      WHERE label.ocr_id = listing_media_ocr.id AND label.superseded_at IS NULL
  );

UPDATE listing_media_ocr o
SET human_classification = label.category_code
FROM ocr_classification_labels label
WHERE label.ocr_id = o.id AND label.superseded_at IS NULL
  AND o.human_classification IS NULL;

COMMIT;
