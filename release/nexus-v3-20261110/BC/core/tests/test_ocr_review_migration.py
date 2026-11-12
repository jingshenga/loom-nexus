import unittest
from pathlib import Path


class OcrReviewMigrationTests(unittest.TestCase):
    def test_migration_never_requeues_raw_for_legacy_extraction(self):
        path = (Path(__file__).parents[2] / "scripts" / "migrations" /
                "2026-11-12-active-ocr-review-assist.sql")
        sql = path.read_text(encoding="utf-8").lower()
        self.assertIn("drop trigger if exists trg_active_raw_after_ocr", sql)
        self.assertIn("drop function if exists requeue_active_raw_after_ocr", sql)
        self.assertNotIn("create trigger trg_active_raw_after_ocr", sql)
        self.assertNotIn("set extract_state", sql)
        self.assertIn("add column if not exists ocr_review_pending", sql)


if __name__ == "__main__":
    unittest.main()
