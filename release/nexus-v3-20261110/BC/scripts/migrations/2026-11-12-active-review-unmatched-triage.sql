-- Allow an administrator to move a selected ordinary no-match record into
-- the existing Active review queue with an auditable event.
BEGIN;
ALTER TABLE rule_active_review_events
    DROP CONSTRAINT IF EXISTS rule_active_review_events_action_check;
ALTER TABLE rule_active_review_events
    ADD CONSTRAINT rule_active_review_events_action_check
    CHECK (action IN ('dismiss','no_action','create_pending_listing','manual_requeue'));
COMMIT;
