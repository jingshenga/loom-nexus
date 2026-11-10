-- Index only disposable Shadow diagnostics; business revisions and rule
-- snapshots remain independently retained.
BEGIN;
CREATE INDEX IF NOT EXISTS idx_rule_shadow_comparisons_observed_at
    ON rule_shadow_comparisons(observed_at);
CREATE INDEX IF NOT EXISTS idx_rule_parse_runs_shadow_created_at
    ON rule_parse_runs(created_at) WHERE run_reason='shadow';
COMMIT;
