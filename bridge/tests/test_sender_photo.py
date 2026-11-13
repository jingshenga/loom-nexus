import asyncio
import base64
import io
import struct
import unittest
import zlib

from PIL import Image

from runner.sender import _payload, _prefixed_text, _resolve_bot, _send_any


def photo_data_url():
    image = Image.new("RGB", (8, 8), "red")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()


class PhotoSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_unique_verified_bot_is_used_as_fallback(self):
        class Pool:
            rows = []

            async def fetch(self, *_args):
                return self.rows

        pool = Pool()
        pool.rows = [{"bot_url": "https://t.me/service_bot?start=abc"}]
        self.assertEqual(await _resolve_bot(pool, 12), "service_bot")
        pool.rows = [
            {"bot_url": "https://t.me/service_bot"},
            {"bot_url": "https://t.me/otherbot"},
        ]
        self.assertIsNone(await _resolve_bot(pool, 12))
        pool.rows = []
        self.assertIsNone(await _resolve_bot(pool, 12))

    def test_each_outbound_message_uses_the_alias_snapshot(self):
        job = {"display_name": "小北", "content": "你好", "is_first": False}
        self.assertEqual(_prefixed_text(job), "【小北】你好")

    def test_prefix_is_not_added_twice_on_retry(self):
        job = {"display_name": "小北", "content": "【小北】你好", "is_first": False}
        self.assertEqual(_prefixed_text(job), "【小北】你好")

    async def test_photo_is_sent_as_named_image_with_caption(self):
        class Client:
            async def send_file(self, target, file, **kwargs):
                self.target = target
                self.file = file
                self.kwargs = kwargs

        client = Client()
        kind, payload = _payload({"media_b64": photo_data_url()})
        await _send_any(client, "recipient", kind, payload, caption="拍摄照片")
        self.assertEqual(client.target, "recipient")
        self.assertEqual(client.file.name, "photo.jpg")
        self.assertEqual(client.kwargs["caption"], "拍摄照片")
        self.assertFalse(client.kwargs.get("force_document", False))

    async def test_truncated_photo_is_rejected_before_telegram(self):
        damaged = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"x" * 188).decode()
        with self.assertRaises(ValueError):
            _payload({"media_b64": damaged})

    async def test_oversized_pixel_count_is_rejected(self):
        output = io.BytesIO()
        Image.new("RGB", (1, 1), "red").save(output, format="PNG")
        damaged = bytearray(output.getvalue())
        damaged[16:24] = struct.pack(">II", 1_000_000, 1_000_000)
        damaged[29:33] = struct.pack(">I", zlib.crc32(damaged[12:29]))
        with self.assertRaises(ValueError):
            _payload({"media_b64": "data:image/png;base64," + base64.b64encode(damaged).decode()})


if __name__ == "__main__":
    unittest.main()
