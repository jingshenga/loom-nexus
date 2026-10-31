import unittest

from app import db
from app.api.ocr_review import EditOcrBody, edit


class _OcrReviewPool:
    async def fetch(self, query, asset_id, fields, normalized_text):
        allowed = {"pending", "not_card", "confirmed"}
        accepted = all(f"'{status}'" in query for status in allowed)
        return [{"id": asset_id}] if accepted else []


class OcrReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_allows_resaving_non_card_and_confirmed_items(self):
        previous_pool = db.get_pool
        db.get_pool = lambda: _OcrReviewPool()
        try:
            for asset_id in (10, 11):
                result = await edit(asset_id, EditOcrBody(fields={}))
                self.assertEqual(result, {"ok": True})
        finally:
            db.get_pool = previous_pool


if __name__ == "__main__":
    unittest.main()
