"""Unified floodgate: per-category buckets stay independent, sliding
windows evict stale stamps, and FloodWait trips the shared account
breaker across every category. Backfill pacing is covered end to end
with fake clients/pools."""
import unittest
from types import SimpleNamespace

from runner import backfill, floodgate
from runner.floodgate import (
    AccountCooldown,
    FloodWaitCooldown,
    SendRateLimiter,
    SlidingWindowGate,
    TelegramRateLimitGate,
    flood_wait_seconds,
    is_flood_wait,
)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# exception classes named exactly like the real Telethon/Pyrogram ones
class FloodWaitError(Exception):
    def __init__(self, seconds):
        self.seconds = seconds


class BucketIsolationTests(unittest.TestCase):
    def test_categories_do_not_interfere(self):
        clock = FakeClock()
        gate = TelegramRateLimitGate(clock=clock, categories={
            "send": (3.0, 20), "history": (1.5, 30), "resolve": (2.0, 12),
        })
        for _ in range(12):  # exactly the resolve per-minute cap
            gate.record("resolve", "acc")
        # send/history buckets untouched, resolve capped
        self.assertEqual(gate.delay_for("send", "acc"), 0.0)
        self.assertEqual(gate.delay_for("history", "acc"), 0.0)
        self.assertGreater(gate.delay_for("resolve", "acc"), 0.0)

    def test_min_gap_and_window_cap_are_independent_gates(self):
        clock = FakeClock()
        gate = TelegramRateLimitGate(clock=clock, categories={"send": (3.0, 2)})
        gate.record("send", "a")
        self.assertAlmostEqual(gate.delay_for("send", "a"), 3.0)  # min gap
        gate.record("send", "a")
        clock.advance(3)  # gap satisfied, but 2 stamps inside the window
        self.assertAlmostEqual(gate.delay_for("send", "a"), 57.0)

    def test_accounts_are_isolated_inside_a_bucket(self):
        clock = FakeClock()
        gate = TelegramRateLimitGate(clock=clock, categories={"history": (1.5, 30)})
        gate.record("history", "acc-1")
        self.assertEqual(gate.delay_for("history", "acc-2"), 0.0)
        self.assertAlmostEqual(gate.delay_for("history", "acc-1"), 1.5)

    def test_window_evicts_stamps_after_a_minute(self):
        clock = FakeClock()
        bucket = SlidingWindowGate("history", 0, 2, clock=clock)
        bucket.record(7)
        clock.advance(5)
        bucket.record(7)
        self.assertAlmostEqual(bucket.delay_for(7), 55.0)
        clock.advance(56)  # both stamps now older than 60s
        self.assertEqual(bucket.delay_for(7), 0.0)


class CooldownBreakerTests(unittest.TestCase):
    def test_trip_dominates_every_category(self):
        clock = FakeClock()
        gate = TelegramRateLimitGate(clock=clock, categories={
            "send": (3.0, 20), "history": (1.5, 30), "resolve": (2.0, 12),
        })
        gate.record("send", "acc")
        wait_s = gate.trip_cooldown("acc", 900)
        self.assertEqual(wait_s, 900)
        for category in ("send", "history", "resolve"):
            self.assertAlmostEqual(gate.delay_for(category, "acc"), 900.0)
        clock.advance(899)
        self.assertAlmostEqual(gate.delay_for("send", "acc"), 1.0)
        clock.advance(2)
        self.assertEqual(gate.delay_for("send", "acc"), 0.0)  # ban window over

    def test_cool_keeps_the_longer_deadline_and_clear_works(self):
        clock = FakeClock()
        cooldown = FloodWaitCooldown(clock=clock)
        cooldown.cool("a", 60)
        clock.advance(10)
        cooldown.cool("a", 20)  # shorter than the remaining 50s
        self.assertAlmostEqual(cooldown.remaining("a"), 50.0)
        cooldown.clear("a")
        self.assertEqual(cooldown.remaining("a"), 0.0)

    def test_flood_wait_helpers(self):
        class FloodWaitError(Exception):
            def __init__(self, seconds):
                self.seconds = seconds

        class FloodWait(Exception):
            def __init__(self, value):
                self.value = value

        self.assertTrue(is_flood_wait(FloodWaitError(5)))
        self.assertTrue(is_flood_wait(FloodWait(5)))
        self.assertFalse(is_flood_wait(ValueError("x")))
        self.assertEqual(flood_wait_seconds(FloodWaitError(42)), 42)
        self.assertEqual(flood_wait_seconds(FloodWait(7)), 7)
        self.assertEqual(flood_wait_seconds(ValueError("x")), 1)


class CompatibilityTests(unittest.TestCase):
    def test_send_rate_limiter_matches_legacy_signature(self):
        limiter = SendRateLimiter(min_gap_s=3, per_minute=20, clock=FakeClock())
        self.assertEqual(limiter.delay_for(1), 0.0)
        limiter.record(1)
        self.assertAlmostEqual(limiter.delay_for(1), 3.0)
        self.assertIsInstance(limiter, SlidingWindowGate)

    def test_account_cooldown_is_the_shared_breaker(self):
        cooldown = AccountCooldown(clock=FakeClock())
        cooldown.cool("a", 30)
        self.assertAlmostEqual(cooldown.remaining("a"), 30.0)
        self.assertIsInstance(cooldown, FloodWaitCooldown)


class WaitGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_sleeps_records_and_reports_delay(self):
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        clock = FakeClock()
        gate = TelegramRateLimitGate(clock=clock, categories={"send": (3.0, 20)})
        waited = await gate.wait("send", "a", fake_sleep)
        self.assertEqual((waited, sleeps), (0.0, []))  # first call: no wait
        waited = await gate.wait("send", "a", fake_sleep)
        self.assertAlmostEqual(waited, 3.0)
        self.assertEqual(sleeps, [3.0])
        # the call was recorded against the injected clock (two stamps now)
        self.assertEqual(len(gate.gate("send")._window["a"]), 2)

    async def test_wait_with_stop_event_bails_out_early(self):
        import asyncio

        clock = FakeClock()
        gate = TelegramRateLimitGate(clock=clock, categories={"send": (30.0, 20)})
        gate.record("send", "a")  # so the next call owes a 30s min-gap
        stop = asyncio.Event()
        stop.set()
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        waited = await gate.wait("send", "a", fake_sleep, stop=stop)
        self.assertAlmostEqual(waited, 30.0)  # reported, but...
        self.assertEqual(sleeps, [])  # never slept: stop fired first


class FakeClient:
    """get_entity/get_messages record their floodgate categories."""

    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    async def get_entity(self, peer_id):
        self.calls.append("resolve")
        return SimpleNamespace(id=peer_id)

    async def get_messages(self, entity, limit=None, offset_id=None, ids=None):
        self.calls.append("history")
        return list(self.messages)


class FakePool:
    def __init__(self, watermark=None):
        self.watermark = watermark
        self.limit_sets = []
        self.appends = []

    async def fetch(self, sql, *args):
        if "MIN(platform_msg_id)" in sql:
            return [self.watermark or {"min_msg_id": 0, "max_msg_id": 0, "total_ingested": 0}]
        if "gateway_limit_set" in sql:
            self.limit_sets.append(args)
            return None
        self.appends.append(args)
        return [{"raw_id": args[1]}]


def msg(mid, text):
    from datetime import datetime, timezone

    return SimpleNamespace(
        id=mid, sender_id=42, grouped_id=None, raw_text=text,
        date=datetime(2026, 11, 20, tzinfo=timezone.utc),
        photo=None, video=None, video_note=None, file=None,
    )


class BackfillPacingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.gate = TelegramRateLimitGate(
            clock=self.clock,
            categories={"resolve": (2.0, 12), "history": (1.5, 30), "media": (0.25, 90)},
        )
        self._orig_gate = backfill.GATE
        backfill.GATE = self.gate

    def tearDown(self):
        backfill.GATE = self._orig_gate

    async def test_history_walk_paces_resolve_and_history_buckets(self):
        client = FakeClient([msg(300, "b"), msg(200, "a")])
        rows = await backfill._fetch_history(
            client, -100, 50, 1, offset_id=250, account_id="acc",
        )
        # both call sites recorded into their own buckets
        self.assertEqual(client.calls, ["resolve", "history"])
        self.assertGreater(self.gate.delay_for("history", "acc"), 0.0)
        self.assertGreater(self.gate.delay_for("resolve", "acc"), 0.0)
        self.assertEqual([r[0] for r in rows], [200, 300])

    async def test_flood_wait_trips_breaker_persists_flag_and_raises_cooling(self):
        class FloodWaitError(Exception):
            def __init__(self, seconds):
                self.seconds = seconds

        client = FakeClient([])
        async def flood(*args, **kwargs):
            client.calls.append("history")
            raise FloodWaitError(600)

        client.get_messages = flood
        pool = FakePool()
        job = {"job_id": 9, "peer_id": -100, "msg_limit": 50, "source_id": 1,
               "offset_id": 250, "account_id": "acc"}
        with self.assertRaises(backfill.BackfillCooling) as ctx:
            await backfill._backfill_history(pool, job, client)
        self.assertEqual(ctx.exception.wait_s, 600)
        # shared breaker tripped: sender-side send bucket is blocked too
        self.assertAlmostEqual(self.gate.delay_for("send", "acc"), 600.0)
        # cooling flag persisted through gateway_limit_set
        self.assertEqual(pool.limit_sets, [("acc", "cooldown")])

    async def test_media_downloads_pass_the_media_bucket(self):
        import tempfile
        from unittest.mock import patch

        downloads = []

        class MediaMsg:
            id = 5
            photo = SimpleNamespace(id=77)
            video = None
            video_note = None
            file = SimpleNamespace(size=10, ext=".jpg")

            async def download_media(self, file):
                downloads.append(file)
                return str(file)

        with patch.object(backfill, "MEDIA_DIR", tempfile.mkdtemp()), \
                patch("runner.media_meta.enrich_entries", lambda entries: entries):
            saved = await backfill.save_media(MediaMsg(), 1, account_id="acc")
        self.assertEqual(len(downloads), 1)
        # the media bucket recorded the download for this account
        media_gate = backfill.GATE.gate("media")
        self.assertEqual(len(media_gate._window.get("acc", [])), 1)


    async def test_official_flood_wait_during_media_download_trips_account(self):
        from unittest.mock import AsyncMock, patch

        import tempfile

        error = type("FloodWaitError", (Exception,), {"seconds": 90})()
        photo = msg(100, "photo")
        photo.photo = SimpleNamespace(id=77)
        photo.download_media = AsyncMock(side_effect=error)
        client = FakeClient([photo])
        pool = FakePool()
        with tempfile.TemporaryDirectory() as directory, patch.object(backfill, "MEDIA_DIR", directory):
            with self.assertRaises(backfill.BackfillCooling):
                await backfill._backfill_history(pool, {
                    "job_id": 9, "peer_id": -100, "msg_limit": 1, "source_id": 1,
                    "account_id": "acc", "offset_id": 0,
                }, client)
        photo.download_media.assert_awaited_once()
        self.assertEqual(self.gate.cooldown_remaining("acc"), 90)
        self.assertEqual(pool.limit_sets, [("acc", "cooldown")])


if __name__ == "__main__":
    unittest.main()
