import unittest

from app.api.listings import _row_to_item


class ListingAttributesTests(unittest.TestCase):
    def row(self, **changes):
        row = {
            "id": 1, "source_id": 1, "nickname": "小雨", "district": "海曙",
            "price_amount": 600, "price_currency": "CNY", "bot_url": "",
            "description": "原始介绍", "media_json": [], "published_at": None,
            "presence_tier": "unknown", "duty_label": None,
            "custom_fields": {}, "evidence_json": {"source": "telegram"},
        }
        row.update(changes)
        return row

    def test_public_attributes_merge_ocr_and_manual_fields_without_raw_evidence(self):
        ocr = [{
            "media_path": "card.jpg", "confidence": 0.92,
            "fields_json": {
                "age_years": 22, "height_cm": 168, "weight_kg": 48,
                "price_1p_text": ["1P600 60分钟"],
                "service_text": ["服务：陪浴", "服务：陪浴"],
                "fixed_intro_terms": ["禁无套"],
            },
            "evidence_json": {"age_years": {"line": "年龄22", "score": 0.8}},
        }]
        item = _row_to_item(self.row(custom_fields={"age_years": "23岁", "features_text": ["长发"]}),
                            is_admin=False, ocr_entries=ocr)
        self.assertEqual(item["attributes"]["profile"],
                         {"age": 23, "height": 168, "weight": 48.0, "bust": None})
        self.assertEqual(item["attributes"]["tariffs"]["p1"], ["600 元 (60分钟)"])
        self.assertEqual(item["attributes"]["services"], ["陪浴", "长发"])
        self.assertEqual(item["attributes"]["restrictions"], ["禁无套"])
        self.assertIsNone(item["description"])
        self.assertNotIn("evidence_json", item)
        self.assertNotIn("ocr_evidence", item)

    def test_missing_and_malformed_fields_degrade_and_admin_gets_evidence(self):
        row = self.row(custom_fields='{"age_years":"oops","service_text":"@private 138 1234 5678 服务"}')
        item = _row_to_item(row, is_admin=True)
        self.assertEqual(item["attributes"]["profile"],
                         {"age": None, "height": None, "weight": None, "bust": None})
        public_item = _row_to_item(row, is_admin=False)
        self.assertEqual(public_item["attributes"]["services"], ["服务"])
        self.assertEqual(item["evidence_json"], {"source": "telegram"})
        self.assertEqual(item["ocr_evidence"], [])
        legacy = _row_to_item(self.row(custom_fields=None, evidence_json=None), is_admin=False)
        self.assertEqual(legacy["attributes"]["tariffs"],
                         {"p1": [], "p2": [], "bt": [], "cash_note": [], "raw_fees": []})
        self.assertEqual(legacy["attributes"]["services"], [])
        self.assertEqual(legacy["attributes"]["restrictions"], [])


if __name__ == "__main__":
    unittest.main()
