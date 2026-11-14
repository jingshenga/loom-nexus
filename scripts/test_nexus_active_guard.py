import json
import os
import unittest
from unittest.mock import patch

from nexus_active_guard import _json_object, anomaly_reason


class ActiveGuardJsonTests(unittest.TestCase):
    def test_jsonb_text_decodes_before_baseline_comparison(self):
        baseline = {
            "binding_signature": "bindings",
            "source2_published_signature": "published",
            "source2_published_count": 0,
        }
        metrics = {
            "peer_id": -1003083173117,
            "modes": [
                {"id": 1, "mode": "legacy"}, {"id": 2, "mode": "active"},
                {"id": 5, "mode": "legacy"}, {"id": 6, "mode": "legacy"},
            ],
            "binding_signature": "bindings",
            "source2_published_signature": "published",
            "source2_published_count": 0,
            "active_errors_15m": 0,
            "active_bytes": 0,
            "active_queue_open": 0,
            "pending_raw": 0,
            "lock_waits": 0,
            "max_transaction_seconds": 0,
            "idle_transactions": 0,
        }
        state = {"active_baseline_json": json.dumps(baseline)}
        with patch.dict(os.environ, {
            "NEXUS_ACTIVE_ROUTER_ENABLED": "true",
            "NEXUS_ACTIVE_ROUTER_KILL_SWITCH": "false",
            "NEXUS_ACTIVE_ROUTER_SOURCE_IDS": "2",
        }, clear=False):
            self.assertIsNone(anomaly_reason(metrics, state))

    def test_invalid_or_non_object_baseline_fails_closed(self):
        self.assertEqual(_json_object("not-json"), {})
        self.assertEqual(_json_object("[]"), {})
        self.assertEqual(_json_object({"ok": True}), {"ok": True})
        self.assertEqual(_json_object(None), {})


if __name__ == "__main__":
    unittest.main()
