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
    ALTER TABLE listing_duplicate_decisions
        ADD CONSTRAINT listing_duplicate_decisions_decision_check
        CHECK (decision IN ('keep_latest', 'keep_selected', 'merge_selected', 'keep_both'));
END
$migration$;
