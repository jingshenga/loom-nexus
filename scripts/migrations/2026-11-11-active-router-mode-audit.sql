BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE rule_router_mode_events
    DROP CONSTRAINT IF EXISTS rule_router_mode_events_previous_mode_check;
ALTER TABLE rule_router_mode_events
    ADD CONSTRAINT rule_router_mode_events_previous_mode_check
    CHECK (previous_mode IN ('legacy', 'shadow', 'active'));

ALTER TABLE rule_router_mode_events
    DROP CONSTRAINT IF EXISTS rule_router_mode_events_new_mode_check;
ALTER TABLE rule_router_mode_events
    ADD CONSTRAINT rule_router_mode_events_new_mode_check
    CHECK (new_mode IN ('legacy', 'shadow', 'active'));

COMMIT;
