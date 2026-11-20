CREATE TABLE IF NOT EXISTS rule_recommendation_dismissals (
    candidate_key VARCHAR(64) PRIMARY KEY,
    dismissed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
