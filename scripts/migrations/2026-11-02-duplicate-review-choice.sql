BEGIN;

ALTER TABLE listing_duplicate_decisions
    DROP CONSTRAINT IF EXISTS listing_duplicate_decisions_decision_check;

ALTER TABLE listing_duplicate_decisions
    ADD CONSTRAINT listing_duplicate_decisions_decision_check
    CHECK (decision IN ('keep_latest', 'keep_selected', 'keep_both'));

COMMIT;
