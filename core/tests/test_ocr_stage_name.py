import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api import ocr_review


class _Connection:
    def __init__(self, ocr_row, update_row=None):
        self.ocr_row = ocr_row
        self.update_row = update_row
        self.update_sql = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def transaction(self):
        return self

    async def fetchrow(self, query, *_args):
        if query.startswith("SELECT o.review_status"):
            return self.ocr_row
        self.update_sql = query
        return self.update_row


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return self.connection


class OcrStageNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_confirmed_info_card_candidate_can_be_applied(self):
        conn = _Connection({
            "review_status": "pending", "classification": "info_card",
            "fields_json": {"stage_name_candidate": "小雨"}, "listing_id": 12,
        }, {"id": 12, "nickname": "小雨"})
        with patch.object(ocr_review.db, "get_pool", return_value=_Pool(conn)):
            with self.assertRaises(HTTPException) as error:
                await ocr_review.apply_stage_name(5)

        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(conn.update_sql)

    async def test_apply_marks_nickname_human_reviewed_and_never_overwrites_it(self):
        conn = _Connection({
            "review_status": "confirmed", "classification": "info_card",
            "fields_json": {"stage_name_candidate": "小雨"}, "listing_id": 12,
        }, {"id": 12, "nickname": "小雨"})
        with patch.object(ocr_review.db, "get_pool", return_value=_Pool(conn)):
            result = await ocr_review.apply_stage_name(5)

        self.assertEqual(result, {"ok": True, "listing_id": 12, "nickname": "小雨"})
        self.assertIn("manual_fields", conn.update_sql)
        self.assertIn("contact_username", conn.update_sql)


if __name__ == "__main__":
    unittest.main()
