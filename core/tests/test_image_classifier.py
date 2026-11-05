import unittest

from app.extractor.image_classifier import as_card_binary_samples, predict, train_and_validate


class ImageClassifierTest(unittest.TestCase):
    def test_card_binary_mapping_keeps_only_info_card_positive(self):
        source = [
            {"id": 1, "label": "info_card", "text": "昵称 小雨 价格 500 联系方式"},
            {"id": 2, "label": "info_card", "text": "资料卡 昵称 小兰 价格 600"},
            {"id": 3, "label": "info_card", "text": "地区 宁波 联系方式 价格 700"},
            {"id": 4, "label": "profile_photo", "text": "", "signals": {"face_score": 0.8}},
            {"id": 5, "label": "advertisement", "text": "推广 优惠 关注公众号"},
            {"id": 6, "label": "chat_screenshot", "text": "聊天记录 你好 在吗"},
        ]

        mapped = as_card_binary_samples(source)
        model, metrics = train_and_validate(mapped, ["info_card", "other"])

        self.assertEqual([item["label"] for item in mapped], ["info_card", "info_card", "info_card", "other", "other", "other"])
        self.assertEqual(model["classes"], ["info_card", "other"])
        self.assertEqual(set(metrics["per_class"]), {"info_card", "other"})
        self.assertEqual([item["label"] for item in source], ["info_card", "info_card", "info_card", "profile_photo", "advertisement", "chat_screenshot"])

    def test_trains_multiple_categories_and_returns_probability(self):
        samples = [
            {"id": 1, "label": "info_card", "text": "昵称 小雨 价格 500 位置 宁波 联系方式"},
            {"id": 2, "label": "info_card", "text": "资料卡 价格 800 地区 宁波 微信"},
            {"id": 3, "label": "info_card", "text": "昵称 小兰 价格 600 位置 海曙 联系方式"},
            {"id": 4, "label": "advertisement", "text": "优惠促销 限时活动 关注公众号 抽奖"},
            {"id": 5, "label": "advertisement", "text": "推广 活动优惠 扫码关注 立即报名"},
            {"id": 6, "label": "advertisement", "text": "广告 限时优惠 关注我们 活动报名"},
            {"id": 7, "label": "profile_photo", "text": "", "signals": {"face_score": 0.8, "text_chars": 0}},
            {"id": 8, "label": "profile_photo", "text": "", "signals": {"face_score": 0.9, "text_chars": 0}},
            {"id": 9, "label": "profile_photo", "text": "", "signals": {"face_score": 0.7, "text_chars": 0}},
        ]
        model, metrics = train_and_validate(
            samples, ["info_card", "advertisement", "profile_photo"],
        )

        label, confidence, distribution = predict(
            model, "推广优惠 限时活动 关注公众号",
        )
        self.assertEqual(label, "advertisement")
        self.assertGreater(confidence, 0.5)
        self.assertAlmostEqual(sum(distribution.values()), 1.0)
        self.assertEqual(metrics["validation_count"], 3)
        self.assertEqual(set(metrics["per_class"]), {"info_card", "advertisement", "profile_photo"})

    def test_requires_examples_for_each_class(self):
        with self.assertRaisesRegex(ValueError, "至少需要 2 张"):
            train_and_validate([
                {"id": 1, "label": "info_card", "text": "资料卡"},
                {"id": 2, "label": "advertisement", "text": "广告"},
            ], ["info_card", "advertisement", "profile_photo"])


if __name__ == "__main__":
    unittest.main()
