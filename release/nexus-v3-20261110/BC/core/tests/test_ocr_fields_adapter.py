import unittest

from app.extractor.ocr_fields import equivalent_field, structured_ocr_candidate


class OcrFieldAdapterTests(unittest.TestCase):
    def adapt(self, fields, *, text_identity=""):
        return structured_ocr_candidate(
            fields, {"stage_name": {"line": "昵称：阿青", "score": 0.91}},
            ocr_id=12, engine_version="rapidocr-test", confidence=0.88,
            text_identity=text_identity,
        )

    def test_maps_only_labeled_fields_and_keeps_provenance(self):
        result = self.adapt({
            "stage_name": "阿青",
            "stage_name_candidate": "小青",
            "contact_handles": ["alphauser"],
            "location_text": ["地区：宁波市海曙区"],
            "price_text": ["价格：￥900"],
        }, text_identity="@AlphaUser")
        self.assertEqual(result["fields"], {
            "nickname": "阿青", "username": "alphauser",
            "district": "宁波市海曙区", "price_amount": 900,
            "price_currency": "CNY",
        })
        self.assertEqual(result["evidence"]["price_amount"]["ocr_id"], 12)
        self.assertEqual(result["clues"]["stage_name_candidate"]["values"], ["小青"])

    def test_candidate_name_and_ambiguous_contact_are_not_selected(self):
        result = self.adapt({
            "stage_name_candidate": "候选名",
            "contact_handles": ["alphauser", "betaguy"],
            "price_text": ["价格：900"],
        })
        self.assertNotIn("nickname", result["fields"])
        self.assertNotIn("username", result["fields"])
        self.assertIn("price_text", result["clues"])
        self.assertEqual(result["conflicts"][0]["kind"], "telegram_identity")

    def test_contact_must_match_text_identity_when_text_identity_exists(self):
        result = self.adapt({"contact_handles": ["betaguy"]}, text_identity="alphauser")
        self.assertNotIn("username", result["fields"])
        self.assertEqual(result["conflicts"][0]["values"], ["alphauser", "betaguy"])

    def test_price_requires_semantic_label_and_explicit_unit(self):
        result = self.adapt({"price_text": ["价格：900", "附近有地铁"]})
        self.assertNotIn("price_amount", result["fields"])
        result = self.adapt({"price_text": ["服务 900 元"], "stage_name": "阿青"})
        self.assertNotIn("price_amount", result["fields"])

    def test_multiple_labeled_prices_remain_an_explicit_conflict(self):
        result = self.adapt({"price_text": ["价格：900元", "价格：1200元"]})
        self.assertNotIn("price_amount", result["fields"])
        self.assertEqual(result["conflicts"][0]["field"], "price_amount")
        self.assertEqual(result["conflicts"][0]["values"], [900, 1200])

    def test_normalized_equivalence(self):
        self.assertTrue(equivalent_field("username", "@AlphaUser", "alphauser"))
        self.assertTrue(equivalent_field("price_amount", 900, 900.0))
        self.assertTrue(equivalent_field("district", "宁波 市", "宁波市"))
        self.assertFalse(equivalent_field("price_amount", 900, 901))


if __name__ == "__main__":
    unittest.main()
