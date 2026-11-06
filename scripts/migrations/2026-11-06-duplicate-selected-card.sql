ALTER TABLE listing_duplicate_decisions
    ADD COLUMN IF NOT EXISTS kept_listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL;

DO $migration$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'listing_duplicate_decisions_decision_check'
          AND conrelid = 'listing_duplicate_decisions'::regclass
    ) THEN
        ALTER TABLE listing_duplicate_decisions
            DROP CONSTRAINT listing_duplicate_decisions_decision_check;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'listing_duplicate_decisions_decision_check'
          AND conrelid = 'listing_duplicate_decisions'::regclass
    ) THEN
        ALTER TABLE listing_duplicate_decisions
            ADD CONSTRAINT listing_duplicate_decisions_decision_check
            CHECK (decision IN ('keep_latest', 'keep_selected', 'keep_both'));
    END IF;
END
$migration$;
