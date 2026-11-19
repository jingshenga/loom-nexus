"""Cursor-based history backfill: offset passthrough, watermark
resolution, and the idempotent append loop."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from runner.backfill import _fetch_history, _resolve_offset


class FakeClient:
    """Records get_messages kwargs; returns newest-first fake messages."""

    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    async def get_entity(self, peer_id):
        return SimpleNamespace(id=peer_id)

    async def get_messages(self, entity, limit=None, offset_id=None, ids=None):
        self.calls.append({"limit": limit, "offset_id": offset_id, "ids": ids})
        return list(self.messages)


class FakePool:
    def __init__(self, watermark=None):
        self.watermark = watermark
        self.appends = []

    async def fetch(self, sql, *args):
        if "MIN(platform_msg_id)" in sql:
            return [self.watermark or {"min_msg_id": 0, "max_msg_id": 0, "total_ingested": 0}]
        self.appends.append(args)
        return [{"raw_id": args[1]}]  # new rows insert successfully


def msg(mid, text, grouped=None):
    return SimpleNamespace(
        id=mid, sender_id=42, grouped_id=grouped, raw_text=text,
        date=datetime(2026, 11, 19, tzinfo=timezone.utc),
        photo=None, video=None, video_note=None, file=None,
    )


class OffsetPassthroughTests(unittest.IsolatedAsyncioTestCase):
    async def test_offset_id_is_passed_to_telethon(self):
        client = FakeClient([msg(300, "新消息"), msg(200, "旧消息")])
        rows = await _fetch_history(client, -100, 50, 1, offset_id=250)
        self.assertEqual(client.calls[0]["offset_id"], 250)
        self.assertEqual(client.calls[0]["limit"], 50)
        # newest-first payload arrives oldest-first after the reversal
        self.assertEqual([r[0] for r in rows], [200, 300])

    async def test_default_offset_is_zero_newest_page(self):
        client = FakeClient([msg(9, "x")])
        await _fetch_history(client, -100, 10, 1)
        self.assertEqual(client.calls[0]["offset_id"], 0)

    async def test_album_parts_merge_into_one_row(self):
        client = FakeClient([
            msg(12, "", grouped=777), msg(11, "相册说明 1P600", grouped=777),
        ])
        rows = await _fetch_history(client, -100, 10, 1, offset_id=20)
        self.assertEqual(len(rows), 1)
        anchor_id, _, grouped, text, _, _ = rows[0]
        self.assertEqual((anchor_id, grouped), (11, 777))
        self.assertIn("1P600", text)


class ResolveOffsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_positive_offset_wins(self):
        pool = FakePool(watermark={"min_msg_id": 123, "max_msg_id": 9, "total_ingested": 1})
        offset = await _resolve_offset(pool, {"offset_id": 777, "source_id": 1})
        self.assertEqual(offset, 777)

    async def test_deep_mode_resolves_local_watermark(self):
        pool = FakePool(watermark={"min_msg_id": 123, "max_msg_id": 900, "total_ingested": 5})
        offset = await _resolve_offset(pool, {"offset_id": -1, "source_id": 1})
        self.assertEqual(offset, 123)

    async def test_zero_and_missing_mean_newest(self):
        pool = FakePool()
        self.assertEqual(await _resolve_offset(pool, {"offset_id": 0, "source_id": 1}), 0)
        self.assertEqual(await _resolve_offset(pool, {"source_id": 1}), 0)


class IdempotentAppendTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_receives_cursor_page_rows(self):
        # append loop feeds every seen message into ingest_append_sample;
        # the SQL function's ON CONFLICT decides new-vs-duplicate, and a
        # NULL raw_id means the message was already stored locally
        from runner.backfill import _backfill_history

        class Pool(FakePool):
            def __init__(self):
                super().__init__()
                self.seen = {300}  # 300 already stored locally (duplicate)

            async def fetch(self, sql, *args):
                if "MIN(platform_msg_id)" in sql:
                    return [{"min_msg_id": 0, "max_msg_id": 0, "total_ingested": 0}]
                if args[1] in self.seen:
                    return [{"raw_id": None}]  # duplicate -> skipped
                self.seen.add(args[1])
                return [{"raw_id": args[1]}]

        client = FakeClient([msg(300, "重复消息"), msg(200, "全新消息")])
        job = {"peer_id": -100, "msg_limit": 50, "source_id": 1, "offset_id": 0}
        inserted = await _backfill_history(Pool(), job, client)
        self.assertEqual(inserted, 1)  # the duplicate was skipped, not re-inserted


if __name__ == "__main__":
    unittest.main()
