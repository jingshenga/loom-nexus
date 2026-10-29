import unittest

from app.api.listings import _visible_media
from app.extractor.media_ocr import classify_image, extract_card_fields, normalize_ocr_text


class MediaOcrTests(unittest.TestCase):
    def test_normalization_removes_decorative_emoji_but_keeps_markers(self):
        value = normalize_ocr_text("🌹 服务：胸围 C+ @sample 1p500 禁无套")
        self.assertEqual(value, "服务:胸围 C+ @sample 1p500 禁无套")

    def test_dense_labelled_text_is_classified_as_private_card(self):
        lines = [
            {"text": "甜思雨"},
            {"text": "身高：165"},
            {"text": "体重：45"},
            {"text": "胸围：C"},
            {"text": "课费：1p500 2p900"},
            {"text": "服务：陪浴 丝袜 高跟鞋"},
            {"text": "禁无套 禁装机"},
        ]
        classification, confidence = classify_image(lines, 0.0, 0.72)
        self.assertEqual(classification, "info_card")
        self.assertGreaterEqual(confidence, 0.8)

    def test_short_caption_on_a_face_photo_remains_public(self):
        classification, _ = classify_image(
            [{"text": "周末快乐"}], 0.64, 0.35,
        )
        self.assertEqual(classification, "profile_photo")

    def test_fields_keep_explicit_age_and_do_not_turn_birth_year_into_age(self):
        lines = [
            {"text": "年龄：19"},
            {"text": "身高：165cm"},
            {"text": "体重：90斤"},
            {"text": "胸围：B+"},
            {"text": "02年"},
            {"text": "课费：P800 45分钟"},
        ]
        fields, evidence = extract_card_fields(lines)
        self.assertEqual(fields["age_years"], 19)
        self.assertEqual(fields["height_cm"], 165)
        self.assertEqual(fields["weight_kg"], 45)
        self.assertEqual(fields["bust_size"], "B+")
        self.assertEqual(fields["age_years"], 19)
        self.assertIn("02年", fields["unparsed_year_text"][0])
        self.assertIn("age_years", evidence)

    def test_fields_capture_explicit_features_prices_and_fixed_intro_only(self):
        lines = [
            {"text": "价格：1P800 2P1500"},
            {"text": "服务：陪浴"},
            {"text": "特点：长发"},
            {"text": "禁嗑药 禁无套 禁醉酒 禁桩基 禁拔套 禁鸽子 禁变态"},
            {"text": "请携带现金"},
        ]
        fields, _ = extract_card_fields(lines)
        self.assertEqual(fields["features_text"], ["特点：长发"])
        self.assertEqual(fields["price_1p_text"], ["价格：1P800 2P1500"])
        self.assertEqual(fields["price_2p_text"], ["价格：1P800 2P1500"])
        self.assertEqual(fields["fixed_intro_terms"], [
            "禁嗑药", "禁无套", "禁醉酒", "禁桩基", "禁拔套", "禁鸽子", "禁变态",
        ])
        self.assertEqual(fields["cash_text"], ["请携带现金"])
        self.assertNotIn("stage_name_candidate", fields)

    def test_non_admin_media_does_not_include_unclassified_photos(self):
        media = [
            {"kind": "photo", "path": "card.jpg", "visibility": "admin"},
            {"kind": "photo", "path": "pending.jpg", "visibility": "pending"},
            {"kind": "photo", "path": "portrait.jpg", "visibility": "public"},
            {"kind": "video", "path": "clip.mp4"},
        ]
        public = _visible_media(media, False)
        self.assertEqual([item["path"] for item in public], ["portrait.jpg", "clip.mp4"])
        self.assertEqual(_visible_media(media, True), media)


if __name__ == "__main__":
    unittest.main()
