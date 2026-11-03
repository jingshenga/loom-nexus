import unittest

from fastapi import HTTPException

from app.api.review import _normalize_bot_url


class BotUrlTests(unittest.TestCase):
    def test_username_is_saved_as_telegram_link(self):
        self.assertEqual(_normalize_bot_url("samplebot"), "https://t.me/samplebot")
        self.assertEqual(_normalize_bot_url("@samplebot"), "https://t.me/samplebot")
        self.assertEqual(_normalize_bot_url("https://t.me/samplebot"), "https://t.me/samplebot")

    def test_invalid_bot_url_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            _normalize_bot_url("https://example.com/samplebot")
        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
