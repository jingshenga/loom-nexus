import unittest

from app.extractor.ast_rules import extract_fields


class ExtractionRulesTest(unittest.TestCase):
    def test_removes_leading_hash_from_nickname_and_district(self):
        rule = {
            "extract": {
                "nickname": [r"艺名[:：]\s*([^\s\n]+)"],
                "district": [r"位置[:：]\s*([^\s\n]+)"],
            },
        }

        fields, _ = extract_fields(rule, "艺名：#小布丁\n位置：#鄞州")

        self.assertEqual(fields["nickname"], "小布丁")
        self.assertEqual(fields["district"], "鄞州")


class ListingNormalizationTests(unittest.TestCase):
    def test_birth_years_and_compact_profile_layouts(self):
        cases = {
            "1998年": 28, "2004年": 22, "07年": 19, "98年": 28,
            "出生年份：０５": 21, "年纪:05": 21, "出生：81": 45,
            "年龄：08年": 18, "05 / 168cm": 21, "身高168cm | 07": 19,
            "07/168/110": 19, "02年\n年龄19": 19, "07年身高168cm": 19,
            "168cm98年": 28, "【2004年】": 22, "05": 21, "05\n身高168": 21,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                fields, _ = extract_fields({}, text)
                self.assertEqual(fields["age_years"], expected)

    def test_age_rejects_implausible_years_and_unrelated_numbers(self):
        for text in ("09年", "26年", "80年", "1970年", "2027年", "出生年份:55",
                     "年龄:17岁", "年龄:46", "年龄:19980", "房号05", "电话尾号\n05", "房号07年",
                     "从业07年", "12位编号1998年", "价格07/168", "年龄:-19"):
            with self.subTest(text=text):
                self.assertNotIn("age_years", extract_fields({}, text)[0])

    def test_weight_units_and_inclusive_bounds(self):
        cases = {
            "110kg": 55.0, "１０５公斤": 52.5, "110": 55.0, "80斤": 40.0,
            "160kg": 80.0, "80.1": 40.0, "38": 38.0, "74.9公斤": 74.9,
            "48.5kg": 48.5, "60斤": 30.0, "38斤": 19.0,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                fields, _ = extract_fields({}, f"体重：{value}")
                self.assertEqual(fields["weight_kg"], expected)
        fields, _ = extract_fields({}, "体重110kg 168cm")
        self.assertEqual(fields["weight_kg"], 55.0)
        for value in ("37.9", "75", "79.9kg", "160.1", "165斤", "110磅",
                      "110lb", "110 lb", "110.5.2", "110/斤", "-50"):
            with self.subTest(value=value):
                self.assertNotIn("weight_kg", extract_fields({}, f"体重：{value}")[0])

    def test_tariff_aliases_with_amount_and_duration(self):
        tiers = {
            "1p": ("1P", "1p", "1次", "一次价格", "一次", "单次", "单次价格", "1次价格"),
            "2p": ("2P", "2p", "2次", "两次价格", "两次", "双次", "双次价格", "2次价格", "pp"),
        }
        rule = {"extract": {"price": [r"价格:?(\d+)"]}}
        for tier, aliases in tiers.items():
            for alias in aliases:
                for money in ("6章", "6米", "6张", "0.6k", "6c", "600元"):
                    with self.subTest(alias=alias, money=money):
                        fields, evidence = extract_fields(rule, f"{alias}{money}40分钟")
                        self.assertEqual(fields[f"price_{tier}"], 600)
                        self.assertEqual(fields[f"price_{tier}_display"], "600 元 (40分钟)")
                        self.assertEqual(fields["price_amount"], 600)
                        self.assertEqual(evidence[f"price_{tier}"]["src"], "regex")

    def test_tariff_keeps_configured_fee_priority_and_rejects_bare_duration(self):
        rule = {"extract": {"price": [r"(?:课费|价格|1P):?(\s*\d+)"]}}
        fields, _ = extract_fields(rule, "1P6章40分钟")
        self.assertEqual(fields["price_amount"], 600)
        fields, _ = extract_fields(rule, "课费500\n单次6章40分钟")
        self.assertEqual(fields["price_amount"], 500)
        self.assertEqual(fields["price_1p"], 600)
        self.assertNotIn("price_amount", extract_fields(rule, "单次价格40分钟")[0])

    def test_tariff_durations_stay_with_their_tier(self):
        fields, _ = extract_fields({}, "一次600 / 双次12章90分钟")
        self.assertEqual(fields["price_1p_display"], "600 元")
        self.assertEqual(fields["price_2p_display"], "1200 元 (90分钟)")
        fields, _ = extract_fields({}, "一次600 / 单次800 45分钟")
        self.assertEqual(fields["price_1p_display"], "600 元")
        fields, _ = extract_fields({}, "1P 600 2P")
        self.assertNotIn("price_2p", fields)
        for text in ("一次40分钟", "双次168cm", "一次13812345678", "单次3张照片", "一次600斤"):
            with self.subTest(text=text):
                self.assertNotIn("price_amount", extract_fields({}, text)[0])

    def test_bust_aliases_preserve_only_the_cup(self):
        cases = {"🐻天然C": "C", "熊：36C": "C", "大熊D": "D", "D罩杯": "D",
                 "Cup：ｈ": "H", "胸围 36C": "C", "胸：B+": "B+", "🐻️天然I": "I"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                fields, _ = extract_fields({}, text)
                self.assertEqual(fields["bust_size"], expected)
        for text in ("胸：J", "cupcake", "🐻Blue", "罩杯103C", "普通熊猫", "胸围88cm"):
            with self.subTest(text=text):
                self.assertNotIn("bust_size", extract_fields({}, text)[0])

class FlashTextRegressionTests(unittest.TestCase):
    def setUp(self):
        from app.extractor import ast_rules

        self.ast = ast_rules
        self.ast.clear_dynamic_terms()
        self.addCleanup(self.ast.clear_dynamic_terms)

    def test_longest_and_adjacent_aliases_with_emoji_and_punctuation(self):
        self.ast.apply_dynamic_terms([
            {"category": "bust_alias", "source_term": "熊", "target_action": "胸"},
            {"category": "bust_alias", "source_term": "大熊", "target_action": "胸围"},
            {"category": "age_alias", "source_term": "【龄】", "target_action": "年龄"},
            {"category": "bust_alias", "source_term": "🐻️", "target_action": "罩杯"},
        ])
        self.assertEqual(self.ast.normalize_input("大熊🐻️【龄】07年"), "胸围罩杯年龄07年")
        self.assertEqual(self.ast.find_age(["【龄】07年"]), (0, 19))
        self.assertEqual(self.ast.find_bust("大熊B36"), "B")
        self.assertEqual(self.ast.find_bust("天然36C"), "C")

    def test_built_in_money_units_guard_duration_bust_and_photo_contexts(self):
        self.assertEqual(self.ast.normalize_input("6章 6张 11k 1.1k 1w 10K"), "600 600 1100 1100 10000 10000")
        self.assertEqual(self.ast.normalize_input("照片3张照片 身高1米68 体重51kg 天然36C"), "照片3张照片 身高1米68 体重51kg 天然36C")
        quotes = self.ast.tariff_quotes(["BT 4张50分钟 / 一次6章40分钟 / 900PP(90min)"])
        self.assertEqual({key: (quote["amount"], quote["minutes"]) for key, quote in quotes.items()},
                         {"bt": (400, 50), "1p": (600, 40), "2p": (900, 90)})

    def test_labels_merge_registry_config_and_unstructured_restrictions(self):
        self.ast.apply_dynamic_terms([
            {"category": "restriction_alias", "source_term": "🔕客服免聊", "target_action": "不闲聊无口嗨"},
            {"category": "service_alias", "source_term": "私约课", "target_action": "预约课程"},
        ])
        line = "🔕客服免聊、只接受预约、禁影响邻居休息、私约课通宵"
        _, evidence = self.ast.extract_fields(
            {"extract": {"restriction_text": {"只接受预约": "仅接受预约"}}}, line,
        )
        self.assertEqual(evidence["restriction_text"]["value"], ["不闲聊无口嗨", "仅接受预约", "禁影响邻居休息"])
        self.assertEqual(evidence["service_text"]["value"], ["预约课程", "包夜"])
        self.assertEqual(evidence["restriction_text"]["line"], line)


if __name__ == "__main__":
    unittest.main()
