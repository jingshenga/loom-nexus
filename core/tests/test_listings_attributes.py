import unittest

from app.api.listings import (
    _merge_card_fields,
    _redact,
    _row_attributes,
    build_attributes,
    _sanitize_attributes,
)


class BuildAttributesTests(unittest.TestCase):
    def test_evidence_dict_values_and_card_lists_merge(self):
        evidence = {
            "age_years": {"value": 22, "src": "ocr", "ocr_confidence": 0.94},
            "height_cm": {"value": 168, "src": "regex"},
            "weight_kg": {"value": 48.0, "src": "ocr"},
            "bust_size": {"value": "C", "src": "ocr"},
        }
        card_fields = {
            "price_1p_text": ["课费：1P800 2P1500"],
            "price_2p_text": ["价格：2P1500"],
            "cash_text": ["请携带现金"],
            "price_text": ["水费600"],
            "service_text": ["服务：陪浴 丝袜 高跟鞋"],
            "features_text": ["特点：长发"],
            "restriction_text": ["禁嗑药 禁无套 禁醉酒"],
        }
        attributes = build_attributes(evidence, card_fields)
        self.assertEqual(attributes["profile"], {
            "age": 22, "height": 168, "weight": 48.0, "bust": "C",
        })
        self.assertEqual(attributes["tariffs"]["p1"], ["课费：1P800 2P1500"])
        self.assertEqual(attributes["tariffs"]["p2"], ["价格：2P1500"])
        self.assertEqual(attributes["tariffs"]["cash_note"], ["请携带现金"])
        self.assertIn("水费600", attributes["tariffs"]["raw_fees"])
        self.assertEqual(attributes["services"], ["陪浴", "丝袜", "高跟鞋", "长发"])
        self.assertEqual(attributes["restrictions"], ["禁嗑药", "禁无套", "禁醉酒"])

    def test_missing_fields_degrade_to_empty_not_error(self):
        attributes = build_attributes({}, {})
        self.assertEqual(attributes["profile"], {
            "age": None, "height": None, "weight": None, "bust": None,
        })
        self.assertEqual(attributes["tariffs"], {"p1": [], "p2": [], "cash_note": [], "raw_fees": []})
        self.assertEqual(attributes["services"], [])
        self.assertEqual(attributes["restrictions"], [])

    def test_invalid_values_never_raise(self):
        attributes = build_attributes(
            {"age_years": "abc", "height_cm": {"value": None}},
            {"weight_kg": "not-a-number", "bust_size": 42},
        )
        self.assertIsNone(attributes["profile"]["age"])
        self.assertIsNone(attributes["profile"]["height"])
        self.assertIsNone(attributes["profile"]["weight"])
        self.assertEqual(attributes["profile"]["bust"], "42")


class MergeCardFieldsTests(unittest.TestCase):
    def test_scalar_first_wins_and_lists_union(self):
        cards = [
            {"fields": {"height_cm": 165, "price_1p_text": ["课费：1P800"]}},
            {"fields": {"height_cm": 170, "price_1p_text": ["特价1P600", "课费：1P800"]}},
            {"fields": "not-a-dict"},
        ]
        merged = _merge_card_fields(cards)
        self.assertEqual(merged["height_cm"], 165)  # best-confidence first
        self.assertEqual(merged["price_1p_text"], ["课费：1P800", "特价1P600"])

    def test_legacy_rows_without_columns_degrade(self):
        row = {"id": 1, "nickname": "小甜", "source_id": 2}
        attributes = _row_attributes(row, is_admin=False)
        self.assertEqual(attributes["profile"]["age"], None)
        self.assertEqual(attributes["tariffs"]["p1"], [])


class SanitizeTests(unittest.TestCase):
    def test_handles_and_phones_are_redacted_for_regular_users(self):
        attributes = build_attributes({}, {
            "price_1p_text": ["课费800 找@abaoggio 预约"],
            "cash_text": ["电话 13812345678 现金"],
        })
        safe = _sanitize_attributes(attributes)
        self.assertNotIn("abaoggio", str(safe))
        self.assertNotIn("13812345678", str(safe))
        self.assertIn("[联系]", safe["tariffs"]["p1"][0])
        # numbers inside fee quotes survive ("1P800" is not a phone)
        self.assertNotIn("[联系]", _redact("课费：1P800 2P1500"))

    def test_redact_covers_tme_links(self):
        self.assertEqual(_redact("t.me/abaoggio"), "[联系]")


if __name__ == "__main__":
    unittest.main()
