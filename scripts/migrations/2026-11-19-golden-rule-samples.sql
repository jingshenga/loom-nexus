BEGIN;

-- Keep the snapshot available even when its source listing is deleted.
CREATE TABLE IF NOT EXISTS golden_rule_samples (
    listing_id bigint PRIMARY KEY,
    raw_text text NOT NULL,
    sender_peer_id bigint,
    media_json jsonb NOT NULL,
    ocr_json jsonb NOT NULL,
    expected_json jsonb NOT NULL,
    saved_by bigint REFERENCES site_users(id) ON DELETE SET NULL,
    saved_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
