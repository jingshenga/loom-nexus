import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.extractor.pipeline import Extractor


class ActiveOcrAssistTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rule = {
            "gate": {},
            "extract": {
                "username": [r"@([A-Za-z0-9_]{5,32})"],
                "nickname": [r"昵称[:：]\s*([^\s]+)"],
                "price": [r"价格[:：]\s*(\d+)"],
            },
        }
        self.bindings = [{
            "rule_id": 7, "rule_version_id": 71, "rule_type": "listing",
            "is_active": True, "is_default": False, "priority": 10,
            "purpose": "listing", "match_group": "main",
            "mode": "exclusive_first_valid", "config_json": self.rule,
        }]
        self.pool = SimpleNamespace(fetch=AsyncMock())
        self.extractor = Extractor(self.pool, "", False)
        self.row = {
            "source_id": 2, "raw_id": 15, "raw_text": "@alphauser",
            "sender_peer_id": 901, "media_json": [{"kind": "photo", "path": "m/1.jpg"}],
        }

    def ocr_row(self, raw_text, *, classification="info_card", fields=None):
        return {
            "id": 23, "media_path": "m/1.jpg", "status": "done", "classification": classification,
            "review_status": "pending", "raw_text": raw_text,
            "fields_json": fields or {"price_text": ["价格：900"], "service_text": ["服务：A"]},
            "evidence_json": {"price_text": {"line": "价格：900"}}, "confidence": 0.9,
            "engine_version": "rapidocr-local-v1", "updated_at": datetime.now(timezone.utc),
        }

    async def test_ocr_fills_missing_fields_and_keeps_text_identity(self):
        self.pool.fetch.return_value = [self.ocr_row("@alphauser 昵称：阿青 价格：900")]
        text_result = {"selected": {
            "fields": {"username": "alphauser"},
            "evidence": {"username": {"value": "alphauser", "src": "regex"}},
            "identity": "alphauser", "rule_ids": [7], "lifecycle": None,
        }, "trace": [], "conflicts": []}
        result, evidence, pending = await self.extractor._supplement_active_from_ocr(
            self.row, self.bindings, text_result,
        )
        selected = result["selected"]
        self.assertFalse(pending)
        self.assertEqual(selected["fields"], {"username": "alphauser", "nickname": "阿青", "price_amount": 900.0})
        self.assertEqual(selected["evidence"]["username"]["src"], "regex")
        self.assertEqual(selected["evidence"]["price_amount"]["src"], "ocr")
        self.assertEqual(evidence[0]["engine_version"], "rapidocr-local-v1")

    async def test_ocr_identity_conflict_is_preserved_for_review(self):
        self.pool.fetch.return_value = [self.ocr_row("@betaguy 昵称：阿青 价格：900")]
        text_result = {"selected": {
            "fields": {"username": "alphauser"}, "evidence": {},
            "identity": "alphauser", "rule_ids": [7],
        }, "trace": [], "conflicts": []}
        result, _, _ = await self.extractor._supplement_active_from_ocr(
            self.row, self.bindings, text_result,
        )
        self.assertEqual(result["conflicts"][0]["kind"], "telegram_identity")
        self.assertEqual(result["selected"]["identity"], "alphauser")

    async def test_structured_fields_json_fills_missing_candidate_fields(self):
        item = self.ocr_row("", fields={
            "stage_name": "阿青", "contact_handles": ["alphauser"],
            "location_text": ["地区：宁波市海曙区"],
            "price_text": ["价格：900元"],
        })
        self.pool.fetch.return_value = [item]
        result, evidence, pending = await self.extractor._supplement_active_from_ocr(
            self.row, self.bindings, {"selected": None, "trace": [], "conflicts": []},
        )
        self.assertFalse(pending)
        self.assertEqual(result["selected"]["fields"], {
            "nickname": "阿青", "username": "alphauser", "district": "宁波市海曙区",
            "price_amount": 900, "price_currency": "CNY",
        })
        self.assertEqual(result["selected"]["evidence"]["price_amount"]["src"], "ocr")
        self.assertEqual(evidence[0]["ocr_id"], 23)

    async def test_equivalent_text_and_structured_price_is_not_a_conflict(self):
        self.pool.fetch.return_value = [self.ocr_row(
            "价格：900元", fields={"price_text": ["价格：900元"]},
        )]
        text_result = {"selected": {
            "fields": {"username": "alphauser", "price_amount": 900.0},
            "evidence": {"username": {"src": "regex"}, "price_amount": {"src": "regex"}},
            "identity": "alphauser", "rule_ids": [7],
        }, "trace": [], "conflicts": []}
        result, _, _ = await self.extractor._supplement_active_from_ocr(self.row, self.bindings, text_result)
        self.assertFalse(result["conflicts"])
        self.assertEqual(result["selected"]["fields"]["price_amount"], 900.0)

    async def test_real_price_conflict_keeps_text_value_and_both_evidence(self):
        self.pool.fetch.return_value = [self.ocr_row(
            "价格：900元", fields={"price_text": ["价格：1200元"]},
        )]
        text_result = {"selected": {
            "fields": {"username": "alphauser", "price_amount": 900},
            "evidence": {"username": {"src": "regex"}, "price_amount": {"src": "regex", "line": "价格：900元"}},
            "identity": "alphauser", "rule_ids": [7],
        }, "trace": [], "conflicts": []}
        result, _, _ = await self.extractor._supplement_active_from_ocr(self.row, self.bindings, text_result)
        conflict = next(item for item in result["conflicts"] if item.get("field") == "price_amount")
        self.assertEqual(conflict["values"], [900, 1200])
        self.assertEqual(result["selected"]["fields"]["price_amount"], 900)

    async def test_uncertain_image_without_two_business_signals_is_not_used(self):
        self.pool.fetch.return_value = [self.ocr_row(
            "@alphauser 昵称：阿青 价格：900", classification="uncertain", fields={"other_text": ["text"]},
        )]
        result, evidence, pending = await self.extractor._supplement_active_from_ocr(
            self.row, self.bindings, {"selected": None, "trace": [], "conflicts": []},
        )
        self.assertIsNone(result["selected"])
        self.assertIsNone(evidence)
        self.assertFalse(pending)


if __name__ == "__main__":
    unittest.main()
