import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch


try:
    import asyncpg  # noqa: F401
except ModuleNotFoundError:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object
    sys.modules["asyncpg"] = asyncpg_stub

from runner.backfill import order_media as backfill_order_media
from runner.backfill import can_reuse_original
from runner.listener import order_media as listener_order_media
from runner.protocol import _make_album_handler


class AlbumMessage:
    def __init__(self, message_id, kind, text=""):
        self.id = message_id
        self.sender_id = 42
        self.grouped_id = 700
        self.raw_text = text
        self.date = datetime.now(timezone.utc)
        self.kind = kind


class AlbumIngestTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuse_requires_complete_existing_files_and_same_text(self):
        anchor = AlbumMessage(5256, "photo", "fixture caption")
        anchor.photo = object()
        pool = types.SimpleNamespace(fetchrow=AsyncMock(return_value={
            "raw_text": "fixture caption", "media_json": [{"kind": "photo", "path": "fixture.jpg"}],
        }))
        with tempfile.TemporaryDirectory() as temp, patch("runner.backfill.MEDIA_DIR", temp):
            self.assertFalse(await can_reuse_original(pool, 2, anchor, [anchor]))
            Path(temp, "fixture.jpg").touch()
            self.assertTrue(await can_reuse_original(pool, 2, anchor, [anchor]))
            anchor.raw_text = "fixture revised caption"
            self.assertFalse(await can_reuse_original(pool, 2, anchor, [anchor]))

    async def test_album_is_downloaded_as_one_event_and_keeps_all_media(self):
        messages = [
            AlbumMessage(5256, "video", "昵称：舒舒"),
            AlbumMessage(5257, "photo"),
            AlbumMessage(5258, "photo"),
            AlbumMessage(5259, "photo"),
        ]
        event = types.SimpleNamespace(messages=messages, grouped_id=700)

        class Pipeline:
            def __init__(self):
                self.calls = []

            async def accept(self, **kwargs):
                self.calls.append(kwargs)

        async def save_media(message, source_id):
            score = {5257: 0.3, 5258: 0.6, 5259: 0.9}.get(message.id)
            media = {"kind": message.kind, "path": f"{message.id}.{message.kind}"}
            if score is not None:
                media["score"] = score
            return [media]

        pipeline = Pipeline()
        handler = _make_album_handler(pipeline, 2, client=None)
        with patch("runner.protocol.save_media", side_effect=save_media):
            await handler(event)

        self.assertEqual(len(pipeline.calls), 1)
        stored = pipeline.calls[0]
        self.assertEqual(stored["platform_msg_id"], 5256)
        self.assertEqual(stored["grouped_id"], 700)
        self.assertEqual(stored["text"], "昵称：舒舒")
        self.assertEqual([media["kind"] for media in stored["media"]], [
            "photo", "photo", "photo", "video",
        ])
        self.assertEqual([media["score"] for media in stored["media"][:3]], [
            0.9, 0.6, 0.3,
        ])

    def test_photo_order_uses_face_score_before_video(self):
        media = [
            {"kind": "photo", "path": "low.jpg", "score": 0.3},
            {"kind": "video", "path": "clip.mp4", "poster": "clip.jpg"},
            {"kind": "photo", "path": "high.jpg", "score": 0.9},
            {"kind": "photo", "path": "middle.jpg", "score": 0.6},
        ]
        for order in (listener_order_media, backfill_order_media):
            self.assertEqual(
                [item["path"] for item in order(media)],
                ["high.jpg", "middle.jpg", "low.jpg", "clip.mp4"],
            )


if __name__ == "__main__":
    unittest.main()
