import base64
import io
import unittest

from fastapi import HTTPException
from PIL import Image

from app.api.chat_media import _check_image


class ImageBody:
    def __init__(self, data_url):
        self.data_url = data_url


class ChatImageTests(unittest.TestCase):
    def test_valid_jpeg(self):
        image = Image.new("RGB", (8, 8), "red")
        output = io.BytesIO()
        image.save(output, format="JPEG")
        _check_image(ImageBody("data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()))

    def test_truncated_jpeg(self):
        damaged = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"x" * 188).decode()
        with self.assertRaises(HTTPException) as error:
            _check_image(ImageBody(damaged))
        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
