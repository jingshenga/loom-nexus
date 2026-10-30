CREATE TABLE IF NOT EXISTS listing_duplicate_decisions (
    left_listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    right_listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    decision VARCHAR(16) NOT NULL CHECK (decision IN ('keep_latest', 'keep_both')),
    decided_by BIGINT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (left_listing_id, right_listing_id),
    CHECK (left_listing_id < right_listing_id)
);
