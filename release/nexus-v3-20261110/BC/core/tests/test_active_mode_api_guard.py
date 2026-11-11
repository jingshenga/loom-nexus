import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.api import rules


class ActiveModeApiGuardTests(unittest.IsolatedAsyncioTestCase):
    async def assert_denied(self, settings, source_id=2):
        with patch.object(rules, "_pool", return_value=object()), \
             patch.object(rules, "current_user_id", return_value=7), \
             patch.object(rules, "get_settings", return_value=settings):
            with self.assertRaises(HTTPException) as raised:
                await rules.set_source_router_mode(
                    source_id, rules.RouterModeBody(mode="active"), "Bearer admin-test-token",
                )
        self.assertEqual(raised.exception.status_code, 409)

    async def test_global_switch_must_be_enabled(self):
        await self.assert_denied(SimpleNamespace(
            active_router_enabled=False, active_router_source_ids="2", active_router_kill_switch=False,
        ))

    async def test_emergency_kill_switch_blocks_active(self):
        await self.assert_denied(SimpleNamespace(
            active_router_enabled=True, active_router_source_ids="2", active_router_kill_switch=True,
        ))

    async def test_allowlist_must_contain_only_selected_source(self):
        enabled = SimpleNamespace(
            active_router_enabled=True, active_router_source_ids="2,3", active_router_kill_switch=False,
        )
        await self.assert_denied(enabled, source_id=2)
        await self.assert_denied(SimpleNamespace(
            active_router_enabled=True, active_router_source_ids="2", active_router_kill_switch=False,
        ), source_id=3)


if __name__ == "__main__":
    unittest.main()
