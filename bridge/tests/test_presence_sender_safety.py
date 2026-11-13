import asyncio
import unittest
from unittest.mock import patch

from runner import sender
from runner.presence import _is_user, _username_matches


class PresenceSenderSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_presence_accepts_only_user_entities_and_matching_username(self):
        user = type("User", (), {"username": "Teacher_88", "usernames": []})()
        channel = type("Channel", (), {"username": "Teacher_88"})()
        self.assertTrue(_is_user(user))
        self.assertFalse(_is_user(channel))
        self.assertTrue(_username_matches(user, "@teacher_88"))
        self.assertFalse(_username_matches(user, "@other"))

    async def test_sender_refreshes_accounts_without_restarting_the_loop(self):
        class Pool:
            def __init__(self):
                self.claimed = []

            async def fetch(self, query, account_id):
                self.claimed.append(account_id)
                return []

        pool = Pool()
        stop = asyncio.Event()
        refreshes = iter(([11], [22]))
        sleeps = 0

        async def next_accounts(_pool):
            return next(refreshes)

        async def sleep_and_stop(_stop, _seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                stop.set()

        with (
            patch.dict("os.environ", {"NEXUS_BRIDGE_SIM": "0"}),
            patch.object(sender, "ACCOUNT_REFRESH_S", 0),
            patch.object(sender, "_real_account_ids", side_effect=next_accounts),
            patch.object(sender, "_sleep", side_effect=sleep_and_stop),
        ):
            await sender.run_sender(pool, stop)

        self.assertEqual(pool.claimed, [11, 22])


if __name__ == "__main__":
    unittest.main()
