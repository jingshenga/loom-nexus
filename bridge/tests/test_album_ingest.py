import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


try:
    import asyncpg  # noqa: F401
except ModuleNotFoundError:
    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Pool = object
    sys.modules["asyncpg"] = asyncpg_stub

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
            return [{"kind": message.kind, "path": f"{message.id}.{message.kind}"}]

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


if __name__ == "__main__":
    unittest.main()
