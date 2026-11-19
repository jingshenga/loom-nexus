"""Unit tests for outbound pacing: rate limiter, FloodWait cooldown,
and the sender loop's wait gating."""
import unittest
from unittest.mock import patch

from runner import sender
from runner.sender import (
    AccountCooldown,
    SendRateLimiter,
    _account_wait,
    _flood_wait_seconds,
    _handle_flood_wait,
    _is_flood_wait,
)


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# exception classes are named exactly like the real Telethon/Pyrogram
# errors — the helpers detect them by type(exc).__name__
class FloodWaitError(Exception):
    def __init__(self, seconds):
        self.seconds = seconds


class FloodPremiumWaitError(Exception):
    def __init__(self, seconds):
        self.seconds = seconds


class FloodWait(Exception):
    """Pyrogram-style, carries .value."""

    def __init__(self, value):
        self.value = value


class FakePool:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(args)
        return [{"outcome": "retry"}]


class RateLimiterTests(unittest.TestCase):
    def test_first_send_has_no_delay(self):
        clock = FakeClock()
        limiter = SendRateLimiter(min_gap_s=3, per_minute=20, clock=clock)
        self.assertEqual(limiter.delay_for(1), 0.0)

    def test_min_gap_enforced_after_a_send(self):
        clock = FakeClock()
        limiter = SendRateLimiter(min_gap_s=3, per_minute=20, clock=clock)
        limiter.record(1)
        self.assertAlmostEqual(limiter.delay_for(1), 3.0)
        clock.advance(2)
        self.assertAlmostEqual(limiter.delay_for(1), 1.0)
        clock.advance(1.5)
        self.assertEqual(limiter.delay_for(1), 0.0)

    def test_per_minute_cap_sliding_window(self):
        clock = FakeClock()
        limiter = SendRateLimiter(min_gap_s=0, per_minute=2, clock=clock)
        limiter.record(7)
        clock.advance(5)
        limiter.record(7)
        # two sends inside the window: the third must wait for the
        # oldest stamp to slide out (60s - 5s)
        self.assertAlmostEqual(limiter.delay_for(7), 55.0)
        clock.advance(56)
        self.assertEqual(limiter.delay_for(7), 0.0)

    def test_accounts_are_independent(self):
        clock = FakeClock()
        limiter = SendRateLimiter(min_gap_s=4, per_minute=20, clock=clock)
        limiter.record(1)
        self.assertAlmostEqual(limiter.delay_for(2), 0.0)
        self.assertAlmostEqual(limiter.delay_for(1), 4.0)


class CooldownTests(unittest.TestCase):
    def test_cool_remaining_and_clear(self):
        clock = FakeClock()
        cooldown = AccountCooldown(clock=clock)
        self.assertEqual(cooldown.remaining(1), 0.0)
        cooldown.cool(1, 30)
        self.assertAlmostEqual(cooldown.remaining(1), 30.0)
        clock.advance(29)
        self.assertAlmostEqual(cooldown.remaining(1), 1.0)
        clock.advance(2)
        self.assertEqual(cooldown.remaining(1), 0.0)
        cooldown.cool(1, 10)
        self.assertAlmostEqual(cooldown.remaining(1), 10.0)
        cooldown.clear(1)
        self.assertEqual(cooldown.remaining(1), 0.0)

    def test_cool_keeps_the_longer_deadline(self):
        clock = FakeClock()
        cooldown = AccountCooldown(clock=clock)
        cooldown.cool(1, 60)
        clock.advance(10)
        cooldown.cool(1, 20)  # shorter than the remaining 50s
        self.assertAlmostEqual(cooldown.remaining(1), 50.0)


class FloodWaitHelperTests(unittest.TestCase):
    def test_flood_wait_detection(self):
        self.assertTrue(_is_flood_wait(FloodWaitError(10)))
        self.assertTrue(_is_flood_wait(FloodPremiumWaitError(10)))
        self.assertTrue(_is_flood_wait(FloodWait(10)))
        self.assertFalse(_is_flood_wait(ValueError("nope")))

    def test_wait_seconds_extraction(self):
        self.assertEqual(_flood_wait_seconds(FloodWaitError(42)), 42)
        self.assertEqual(_flood_wait_seconds(FloodPremiumWaitError(7)), 7)
        self.assertEqual(_flood_wait_seconds(FloodWait(33)), 33)
        self.assertEqual(_flood_wait_seconds(ValueError("x")), 1)
        self.assertEqual(_flood_wait_seconds(FloodWaitError(0)), 1)


class FloodWaitHandlingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sender.COOLDOWN.clear("acc")

    def tearDown(self):
        sender.COOLDOWN.clear("acc")

    async def test_flood_wait_cools_account_and_requeues_job(self):
        pool = FakePool()
        job = {"job_id": 55}
        await _handle_flood_wait(pool, "acc", job, FloodWaitError(90))
        # account is cooling for ~90s
        self.assertGreater(sender.COOLDOWN.remaining("acc"), 80)
        # the job was finished as retryable with a cooling_down note
        self.assertEqual(len(pool.calls), 1)
        sql_args = pool.calls[0]
        self.assertFalse(sql_args[1])  # ok=False
        self.assertIn("冷却", sql_args[2])
        self.assertIn("90", sql_args[2])

    async def test_account_wait_prefers_cooldown_over_pacing(self):
        limiter = SendRateLimiter(min_gap_s=3, per_minute=20, clock=FakeClock())
        limiter.record("acc")
        with patch.object(sender, "COOLDOWN", AccountCooldown()), \
                patch.object(sender, "RATE_LIMITER", limiter):
            self.assertAlmostEqual(_account_wait("acc"), 3.0)
            sender.COOLDOWN.cool("acc", 60)
            self.assertAlmostEqual(_account_wait("acc"), 60.0, delta=0.01)
            sender.COOLDOWN.clear("acc")
            self.assertAlmostEqual(_account_wait("acc"), 3.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
