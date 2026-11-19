import asyncio
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException

from app import db
from app.api import chat
from app.api.chat import build_quick_phrases, session_context, _session_context_row


class QuickPhraseTests(unittest.TestCase):
    def test_phrases_use_district_and_nickname_when_known(self):
        phrases = build_quick_phrases("浦东", "甜思雨")
        self.assertEqual(phrases[0], "你好，想咨询甜思雨的项目")
        self.assertIn("请问今日在岗吗？", phrases)
        self.assertIn("目前是在浦东附近吗？", phrases)
        self.assertIn("方便介绍一下服务内容吗？", phrases)

    def test_phrases_degrade_to_generic_without_listing_data(self):
        phrases = build_quick_phrases(None, None)
        self.assertNotIn("目前是在None附近吗？", phrases)
        self.assertNotIn("你好，想咨询None的项目", phrases)
        self.assertTrue(all("？有" not in p for p in phrases))
        self.assertEqual(phrases, ["请问今日在岗吗？", "方便介绍一下服务内容吗？", "现在方便过去吗？"])


class SessionContextRowTests(unittest.TestCase):
    def test_row_mapping_converts_price_and_builds_phrases(self):
        row = {
            "session_id": 9,
            "listing_id": 3,
            "status": "open",
            "nickname": "甜思雨",
            "district": "浦东",
            "price_amount": Decimal("800.00"),
            "price_currency": "CNY",
        }
        context = _session_context_row(row)
        self.assertEqual(context["price_amount"], 800.0)
        self.assertEqual(context["nickname"], "甜思雨")
        self.assertIn("目前是在浦东附近吗？", context["quick_phrases"])

    def test_legacy_session_without_listing_degrades_to_nulls(self):
        row = {
            "session_id": 10,
            "listing_id": None,
            "status": "open",
            "nickname": None,
            "district": None,
            "price_amount": None,
            "price_currency": "CNY",
        }
        context = _session_context_row(row)
        self.assertIsNone(context["listing_id"])
        self.assertIsNone(context["price_amount"])
        self.assertNotIn("目前是在None附近吗？", context["quick_phrases"])


class DegradationTests(unittest.TestCase):
    """未配置数据库 / Telegram Bridge 时的优雅降级：接口给出明确
    HTTP 错误，绝不抛 500 未捕获异常。"""

    def test_context_returns_503_when_database_unavailable(self):
        with patch.object(db, "get_pool", lambda: None):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(session_context(1, ""))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_session_list_returns_503_when_database_unavailable(self):
        with patch.object(db, "get_pool", lambda: None):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(chat.list_sessions(""))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_send_returns_503_when_database_unavailable(self):
        body = chat.SendBody(content="hi")
        with patch.object(db, "get_pool", lambda: None):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(chat.send(1, body, ""))
        self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
