import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.api import review


class _CandidatePool:
    async def fetch(self, _query):
        return [{
            "left_id": 10, "left_status": "duplicate_pending", "left_peer_id": 101,
            "left_username": "older", "left_nickname": "Same", "left_district": "A",
            "left_price": 100, "left_currency": "CNY", "left_description": "text",
            "left_media": [{"path": "1"}, {"path": "2"}, {"path": "3"}],
            "left_source_text": "text", "left_source_title": "source",
            "left_collected_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "left_posted_at": None,
            "right_id": 11, "right_status": "published", "right_peer_id": 102,
            "right_username": "newer", "right_nickname": "Same", "right_district": "A",
            "right_price": 100, "right_currency": "CNY", "right_description": "text",
            "right_media": [{"path": "4"}], "right_source_text": "text",
            "right_source_title": "source",
            "right_collected_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
            "right_posted_at": None, "match_reason": "昵称一致，待核对",
        }]


class _DecisionConnection:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def transaction(self):
        return self

    async def fetch(self, _query, _ids):
        return self.rows

    async def fetchval(self, _query, _ids):
        return False

    async def execute(self, query, *args):
        self.executed.append((query, args))


class _DecisionPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return self.connection


class DuplicateReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_media_count_recommends_older_card_over_newer_card(self):
        with patch.object(review.db, "get_pool", return_value=_CandidatePool()):
            result = await review.duplicate_candidates()

        pair = result["items"][0]
        self.assertEqual(pair["suggested_id"], 10)
        self.assertEqual(pair["left"]["media_count"], 3)
        self.assertEqual(pair["right"]["media_count"], 1)
        self.assertEqual(pair["match_reason"], "昵称一致，待核对")

    async def test_admin_can_choose_older_card_to_keep(self):
        rows = [
            {"id": 10, "status": "duplicate_pending", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
             "nickname": "Same", "district": "A", "price_amount": 100, "price_currency": "CNY",
             "contact_peer_id": 101, "contact_username": "older", "profile_collection_job_id": 1,
             "source_msg_id": 100, "source_text": "same profile"},
            {"id": 11, "status": "published", "created_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
             "nickname": "Same", "district": "A", "price_amount": 100, "price_currency": "CNY",
             "contact_peer_id": 102, "contact_username": "newer", "profile_collection_job_id": None,
             "source_msg_id": 101, "source_text": "same profile"},
        ]
        connection = _DecisionConnection(rows)
        body = review.DuplicateDecision(decision="keep_selected", preferred_id=10)
        with patch.object(review.db, "get_pool", return_value=_DecisionPool(connection)), \
             patch.object(review, "current_user_id", return_value=7):
            result = await review.decide_duplicate(10, 11, body, "Bearer test")

        self.assertEqual(result["preferred_id"], 10)
        self.assertEqual(result["archived_id"], 11)
        self.assertEqual(connection.executed[0][1][0], 11)
        self.assertEqual(connection.executed[1][1][0], 10)

    async def test_selected_id_must_belong_to_pair(self):
        body = review.DuplicateDecision(decision="keep_selected", preferred_id=12)
        with patch.object(review.db, "get_pool", return_value=_CandidatePool()):
            with self.assertRaises(HTTPException) as error:
                await review.decide_duplicate(10, 11, body, "Bearer test")
        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
