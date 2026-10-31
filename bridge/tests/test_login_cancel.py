import sys
import types
import unittest

try:
    import asyncpg  # noqa: F401
except ModuleNotFoundError:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object
    sys.modules["asyncpg"] = asyncpg_stub

from runner.login import LOGIN_CANCELLED, LoginCancelled, _wait_secret


class LoginCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiter_exits_when_request_is_cancelled(self):
        class Connection:
            async def fetch(self, sql, request_id):
                return [{"secret": LOGIN_CANCELLED}]

        with self.assertRaises(LoginCancelled):
            await _wait_secret(Connection(), "unused", 3, timeout_s=1)

    async def test_waiter_still_returns_submitted_secret(self):
        class Connection:
            async def fetch(self, sql, request_id):
                return [{"secret": "12345"}]

        secret = await _wait_secret(Connection(), "unused", 3, timeout_s=1)
        self.assertEqual(secret, "12345")


if __name__ == "__main__":
    unittest.main()
