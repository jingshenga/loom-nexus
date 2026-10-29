import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from runner import media_meta


class MediaOrientationTests(unittest.TestCase):
    def test_only_corrects_nonzero_rotation_with_clear_face_evidence(self):
        self.assertEqual(
            media_meta._orientation_from_scores({0: 0.2, 90: 0.91, 180: 0.25, 270: 0.3}),
            90,
        )
        self.assertIsNone(
            media_meta._orientation_from_scores({0: 0.7, 90: 0.8, 180: 0.2, 270: 0.2}),
        )
        self.assertIsNone(
            media_meta._orientation_from_scores({0: 0.9, 90: 0.3, 180: 0.2, 270: 0.1}),
        )

    def test_exif_orientation_creates_corrected_copy_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "source.jpg"
            image = Image.new("RGB", (80, 40), "red")
            exif = Image.Exif()
            exif[274] = 6
            image.save(original, exif=exif)

            with patch.object(media_meta, "MEDIA_ROOT", tmp):
                rel, status = media_meta.normalize_photo("source.jpg")
                with Image.open(Path(tmp) / rel) as corrected:
                    corrected_size = corrected.size
                with Image.open(original) as untouched:
                    untouched_size = untouched.size

            self.assertEqual(status, "exif")
            self.assertEqual(corrected_size, (40, 80))
            self.assertEqual(untouched_size, (80, 40))
            self.assertEqual(rel, "source_upright.jpg")


if __name__ == "__main__":
    unittest.main()
