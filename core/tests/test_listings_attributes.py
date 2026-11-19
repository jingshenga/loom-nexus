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
        # dirty fee lines come out as clean display text ("800 元")
        self.assertEqual(attributes["tariffs"]["p1"], ["800 元"])
        self.assertEqual(attributes["tariffs"]["p2"], ["1500 元"])
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

    def test_legacy_weight_uses_raw_metadata_and_canonical_kg_is_stable(self):
        cases = (
            ({"weight_value": 110}, 55.0),
            ({"weight_raw": "体重：110kg", "weight_kg": 110}, 55.0),
            ({"weight_raw": "体重：38斤", "weight_kg": 19}, 19.0),
            ({"weight_kg": 80}, 80.0),
            ({"weight_raw": "体重：80磅", "weight_value": 80}, None),
            ({"weight_value": 79}, None),
        )
        for card, expected in cases:
            with self.subTest(card=card):
                merged = _merge_card_fields([{"fields": card}])
                self.assertEqual(build_attributes({}, merged)["profile"]["weight"], expected)
        evidence = {"weight_kg": {"value": 55, "src": "regex"}}
        self.assertEqual(build_attributes(evidence, {"weight_value": 110}, {"weight_kg": 110})["profile"]["weight"], 110)

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
            "price_1p_text": ["1P800 找@abaoggio 预约"],
            "cash_text": ["电话 13812345678 现金"],
        })
        safe = _sanitize_attributes(attributes)
        # the tiered quote is formatted clean — no handle survives, and
        # the price itself survives ("1P800" is not a phone number)
        self.assertEqual(safe["tariffs"]["p1"], ["800 元"])
        self.assertNotIn("13812345678", str(safe))
        self.assertIn("[联系]", safe["tariffs"]["cash_note"][0])
        self.assertNotIn("[联系]", safe["tariffs"]["p1"][0])

    def test_redact_covers_tme_links(self):
        self.assertEqual(_redact("t.me/abaoggio"), "[联系]")


class TariffFormattingTests(unittest.TestCase):
    def test_structured_display_value_wins_over_dirty_lines(self):
        attributes = build_attributes({}, {
            "price_1p_display": "600 元 (40分钟)",
            "price_1p_text": ["1P6章40分钟 脏串"],
        })
        self.assertEqual(attributes["tariffs"]["p1"], ["600 元 (40分钟)"])

    def test_legacy_dirty_lines_are_reformatted_not_echoed(self):
        attributes = build_attributes({}, {
            "price_1p_text": ["1P6章 40分钟"],
            "price_2p_text": ["PP 11米 90分钟"],
        })
        self.assertEqual(attributes["tariffs"]["p1"], ["600 元 (40分钟)"])
        self.assertEqual(attributes["tariffs"]["p2"], ["1100 元 (90分钟)"])

    def test_combined_ocr_line_displays_distinct_tier_quotes(self):
        line = "单次6章40分钟 双次12章80分钟"
        attributes = build_attributes({}, {"price_1p_text": [line], "price_2p_text": [line]})
        self.assertEqual(attributes["tariffs"]["p1"], ["600 元 (40分钟)"])
        self.assertEqual(attributes["tariffs"]["p2"], ["1200 元 (80分钟)"])

    def test_text_tariff_evidence_is_visible_without_an_ocr_card(self):
        from app.extractor.ast_rules import extract_fields

        _, evidence = extract_fields({}, "单次6章40分钟 双次12章80分钟")
        attributes = build_attributes(evidence, {})
        self.assertEqual(attributes["tariffs"]["p1"], ["600 元 (40分钟)"])
        self.assertEqual(attributes["tariffs"]["p2"], ["1200 元 (80分钟)"])

    def test_manual_tariff_text_wins_over_extraction_evidence(self):
        evidence = {"price_1p_display": {"value": "600 元", "src": "regex"}}
        attributes = build_attributes(evidence, {}, {"price_1p_text": ["单次800元"]})
        self.assertEqual(attributes["tariffs"]["p1"], ["800 元"])

    def test_raw_fees_keep_only_non_tiered_lines(self):
        attributes = build_attributes({}, {
            "price_text": ["1P6章 40分钟", "包夜 2000", "BT 300"],
        })
        self.assertEqual(attributes["tariffs"]["raw_fees"], ["包夜 2000", "BT 300"])

    def test_unparsable_fee_lines_are_dropped_from_p1(self):
        attributes = build_attributes({}, {
            "price_1p_text": ["闲聊一句话没有价格"],
        })
        self.assertEqual(attributes["tariffs"]["p1"], [])


class RestrictionTagTests(unittest.TestCase):
    def test_long_restriction_tags_are_not_truncated(self):
        attributes = build_attributes({}, {
            "restriction_text": ["禁中途拔套 禁吃药偷拍"],
        })
        self.assertIn("禁中途拔套", attributes["restrictions"])
        self.assertIn("禁吃药偷拍", attributes["restrictions"])
        # the old {1,3} cap would have emitted the broken "禁中途拔" /
        # "禁吃药偷" fragments — make sure those never appear
        self.assertNotIn("禁中途拔", attributes["restrictions"])
        self.assertNotIn("禁吃药偷", attributes["restrictions"])


if __name__ == "__main__":
    unittest.main()
