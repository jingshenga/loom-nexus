import unittest

from app.extractor import ast_rules, ocr_fields, pipeline

RULE = {
    "extract": {
        "nickname": [r"艺名[:：]?\s*([^\s，,；;]+)"],
        "username": [r"(?:@|t\.me/)([A-Za-z0-9_]{3,})"],
        "price": [r"(?:课费|价格|水费)[:：]?\s*(\d+(?:\.\d+)?)"],
        "district": [r"(?:地区|城区|地点)[:：]?\s*([^\s，,；;]+)"],
        "currency_map": {"[¥￥]|CNY": "CNY"},
    },
}


class NormalizeInputTests(unittest.TestCase):
    def test_money_slang_rewrites_to_plain_yuan(self):
        self.assertEqual(ast_rules.normalize_input("课费0.8k"), "课费800")
        self.assertEqual(ast_rules.normalize_input("价格 8c"), "价格 800")
        self.assertEqual(ast_rules.normalize_input("包夜3米"), "包夜300")
        self.assertEqual(ast_rules.normalize_input("课费8张"), "课费800")
        self.assertEqual(ast_rules.normalize_input("水费8千"), "水费8000")
        self.assertEqual(ast_rules.normalize_input("1w"), "10000")
        # "章" is a money unit (一张 = 100 元), not a photo counter
        self.assertEqual(ast_rules.normalize_input("6章"), "600")
        self.assertEqual(ast_rules.normalize_input("12章"), "1200")

    def test_emoji_digits_and_marks_fold(self):
        # keycap emojis back to digits, pointing marks to spaces
        self.assertEqual(ast_rules.normalize_input("1️⃣P👉6章 40分钟"), "1P 600 40分钟")
        self.assertEqual(ast_rules.normalize_input("2️⃣:1200"), "2:1200")
        # prohibition emojis fold to the character 禁
        self.assertEqual(ast_rules.normalize_input("🈲偷拍 🚫嗑药"), "禁偷拍 禁嗑药")
        # 米 folds in tariff context but not height context
        self.assertEqual(ast_rules.normalize_input("P 6米"), "P 600")
        self.assertEqual(ast_rules.normalize_input("PP 11米"), "PP 1100")
        self.assertEqual(ast_rules.normalize_input("身高1米68"), "身高1米68")


class TariffSyntaxTests(unittest.TestCase):
    """阶梯资费三种主流语法 + 时长组合 → 结构化 price/元组。"""

    def test_syntax_a_suffix_quote_with_minutes(self):
        quotes = ocr_fields.tariff_quotes(["600/p ( 40min)"])
        self.assertEqual(quotes["1p"]["amount"], 600.0)
        self.assertEqual(quotes["1p"]["minutes"], 40)
        self.assertEqual(quotes["1p"]["text"], "600 元 (40分钟)")

    def test_syntax_a_pp_suffix(self):
        quotes = ocr_fields.tariff_quotes(["900pp (80分钟)"])
        self.assertEqual(quotes["2p"]["amount"], 900.0)
        self.assertEqual(quotes["2p"]["text"], "900 元 (80分钟)")
        self.assertNotIn("1p", quotes)

    def test_syntax_b_bare_prefix_with_mi_slang(self):
        quotes = ocr_fields.tariff_quotes(["P 6米 50分钟"])
        self.assertEqual(quotes["1p"]["amount"], 600.0)
        self.assertEqual(quotes["1p"]["minutes"], 50)
        self.assertEqual(quotes["1p"]["text"], "600 元 (50分钟)")

    def test_syntax_b_pp_prefix_with_mi_slang(self):
        quotes = ocr_fields.tariff_quotes(["PP 11米 90分钟"])
        self.assertEqual(quotes["2p"]["amount"], 1100.0)
        self.assertEqual(quotes["2p"]["text"], "1100 元 (90分钟)")
        self.assertNotIn("1p", quotes)

    def test_syntax_c_slash_and_ci_suffix(self):
        self.assertEqual(ocr_fields.tariff_quotes(["600/p"])["1p"]["amount"], 600.0)
        self.assertEqual(ocr_fields.tariff_quotes(["1次500"])["1p"]["amount"], 500.0)
        self.assertEqual(ocr_fields.tariff_quotes(["600次"])["1p"]["amount"], 600.0)
        self.assertEqual(ocr_fields.tariff_quotes(["2次 1200"])["2p"]["amount"], 1200.0)

    def test_emoji_prefixed_line_end_to_end(self):
        quotes = ocr_fields.tariff_quotes(["1️⃣P👉6章 40分钟"])
        self.assertEqual(quotes["1p"]["amount"], 600.0)
        self.assertEqual(quotes["1p"]["minutes"], 40)
        self.assertEqual(quotes["1p"]["text"], "600 元 (40分钟)")

    def test_both_tiers_in_one_line_with_minutes(self):
        quotes = ocr_fields.tariff_quotes(["1P 600 40分钟 2P 1200 90分钟"])
        self.assertEqual(quotes["1p"]["text"], "600 元 (40分钟)")
        self.assertEqual(quotes["2p"]["text"], "1200 元 (90分钟)")

    def test_card_fields_carry_structured_price_tiers(self):
        fields, _ = ocr_fields.card_to_listing_fields(
            {"price_1p_text": ["1️⃣P👉6章 40分钟"], "price_2p_text": ["900pp (80分钟)"]},
            {}, None,
        )
        self.assertEqual(fields["price_1p"], 600.0)
        self.assertEqual(fields["price_1p_display"], "600 元 (40分钟)")
        self.assertEqual(fields["price_2p"], 900.0)
        self.assertEqual(fields["price_2p_display"], "900 元 (80分钟)")

    def test_plain_scan_skips_lines_with_tiered_quotes(self):
        modes, plain = ocr_fields.amounts_from_lines(["600/p ( 40min)", "现金 500"])
        self.assertEqual(modes, {"1p": 600.0})
        self.assertEqual(plain, 500.0)  # 600/p 不再进 plain 兜底

    def test_height_bust_and_photo_counters_are_not_money(self):
        self.assertEqual(ast_rules.normalize_input("身高1米68"), "身高1米68")
        self.assertEqual(ast_rules.normalize_input("净高：1.5米"), "净高:1.5米")
        self.assertEqual(ast_rules.normalize_input("胸围88C"), "胸围88C")
        self.assertEqual(ast_rules.normalize_input("3张照片"), "3张照片")

    def test_full_width_marks_and_digit_commas_are_folded(self):
        self.assertEqual(ast_rules.normalize_input("课费：８００"), "课费:800")
        self.assertEqual(ast_rules.normalize_input("价格１,５００"), "价格1500")
        self.assertEqual(ast_rules.normalize_input("１P８００"), "1P800")

    def test_extract_fields_sees_normalized_text(self):
        fields, _ = ast_rules.extract_fields(RULE, "课费：０.８ｋ")
        self.assertEqual(fields["price_amount"], 800.0)

    def test_price_capture_with_currency_marks_still_parses(self):
        rule = {"extract": {"price": [r"价格[:：]?\s*([^，,\s]+)"]}}
        fields, _ = ast_rules.extract_fields(rule, "价格¥1,500")
        self.assertEqual(fields["price_amount"], 1500.0)


class ChunkedExtractionTests(unittest.TestCase):
    def test_value_on_its_own_line_satisfies_strict_colon_pattern(self):
        rule = {"extract": {"price": [r"课费[:：](\d+)"]}}
        fields, _ = ast_rules.extract_fields(rule, "甜思雨\n课费\n800\n浦东")
        self.assertEqual(fields["price_amount"], 800.0)

    def test_loose_space_pattern_gets_the_space_variant(self):
        rule = {"extract": {"price": [r"课费\s?(\d+)"]}}
        fields, _ = ast_rules.extract_fields(rule, "课费\n800")
        self.assertEqual(fields["price_amount"], 800.0)

    def test_anchored_pattern_matches_a_later_line(self):
        rule = {"extract": {"price": [r"^价格[:：](\d+)$"]}}
        fields, _ = ast_rules.extract_fields(rule, "先到先得\n价格：600\n勿扰")
        self.assertEqual(fields["price_amount"], 600.0)

    def test_bracket_wrapped_fullwidth_line_extracts(self):
        rule = {"extract": {"price": [r"课费[:：](\d+)"]}}
        fields, _ = ast_rules.extract_fields(rule, "（课费：８００）")
        self.assertEqual(fields["price_amount"], 800.0)

    def test_whole_text_match_keeps_priority(self):
        rule = {"extract": {"price": [r"(?:课费|价格)[:：]?(\d+)"]}}
        fields, _ = ast_rules.extract_fields(rule, "价格500 课费800")
        self.assertEqual(fields["price_amount"], 500.0)

    def test_username_at_tme_and_urls_are_stripped(self):
        fields, _ = ast_rules.extract_fields(RULE, "联系 @abaoggio 课费800")
        self.assertEqual(fields["username"], "abaoggio")
        fields, _ = ast_rules.extract_fields(RULE, "t.me/abaoggio 课费800")
        self.assertEqual(fields["username"], "abaoggio")
        fields, _ = ast_rules.extract_fields(RULE, "https://t.me/abaoggio 课费800")
        self.assertEqual(fields["username"], "abaoggio")
        # a pattern that captures the whole handle still stores it bare
        rule = {"extract": {"username": [r"用户名[:：]?\s*(@?[A-Za-z0-9_]{3,})"]}}
        fields, _ = ast_rules.extract_fields(rule, "用户名：@sunny_girl")
        self.assertEqual(fields["username"], "sunny_girl")


class OcrCrossFillTests(unittest.TestCase):
    CARD = {
        "stage_name": "甜思雨",
        "contact_handles": ["@abaoggio"],
        "location_text": ["地区：浦东"],
        "price_1p_text": ["课费：1P800 2P1500"],
        "height_cm": 165,
        "weight_kg": 45.0,
        "bust_size": "C+",
    }

    def test_card_fields_map_to_listing_fields_with_provenance(self):
        fields, evidence = ocr_fields.card_to_listing_fields(
            self.CARD,
            {"stage_name": {"line": "甜思雨", "score": 0.97}},
            0.9,
        )
        self.assertEqual(fields["nickname"], "甜思雨")
        self.assertEqual(fields["username"], "abaoggio")
        self.assertEqual(fields["district"], "浦东")
        self.assertEqual(fields["price_amount"], 800.0)
        self.assertEqual(fields["height_cm"], 165)
        self.assertEqual(fields["weight_kg"], 45.0)
        self.assertEqual(fields["bust_size"], "C+")
        entry = evidence["nickname"]
        self.assertEqual(entry["src"], "ocr")
        self.assertEqual(entry["ocr_confidence"], 0.9)
        self.assertEqual(entry["line"], "甜思雨")
        self.assertEqual(entry["ocr_line_score"], 0.97)
        self.assertEqual(evidence["price_amount"]["line"], "课费：1P800 2P1500")

    def test_price_modes_prefer_one_person_and_ignore_minutes(self):
        modes, plain = ocr_fields.amounts_from_lines(["课费：1P800 2P1500 45分钟"])
        self.assertEqual(modes, {"1p": 800.0, "2p": 1500.0})
        self.assertIsNone(plain)
        modes, plain = ocr_fields.amounts_from_lines(["现金 600"])
        self.assertEqual(modes, {})
        self.assertEqual(plain, 600.0)

    def test_two_person_quote_used_when_no_single_quote(self):
        fields, evidence = ocr_fields.card_to_listing_fields(
            {"price_2p_text": ["价格：2P1200"]}, {}, None,
        )
        self.assertEqual(fields["price_amount"], 1200.0)
        self.assertNotIn("ocr_confidence", evidence["price_amount"])

    def test_merge_keeps_regex_values_and_fills_missing(self):
        base_fields, base_evidence = ast_rules.extract_fields(RULE, "艺名：小甜 课费700")
        card_fields, card_evidence = ocr_fields.card_to_listing_fields(
            self.CARD, {}, 0.85,
        )
        merged, merged_evidence = ast_rules.merge_extraction(
            base_fields, base_evidence, card_fields, card_evidence,
        )
        self.assertEqual(merged["price_amount"], 700.0)      # regex wins
        self.assertEqual(merged["nickname"], "小甜")          # regex wins
        self.assertEqual(merged["district"], "浦东")          # filled from card
        self.assertEqual(merged_evidence["district"]["src"], "ocr")
        self.assertEqual(merged_evidence["price_amount"]["src"], "regex")

    def test_rule_regex_still_runs_over_raw_ocr_text_with_slang(self):
        fields, evidence = ast_rules.extract_fields(
            RULE, "课费 0.8k 地区：浦东", src="ocr",
        )
        self.assertEqual(fields["price_amount"], 800.0)
        self.assertEqual(fields["district"], "浦东")
        self.assertEqual(evidence["price_amount"]["src"], "ocr")


class PipelineTriggerTests(unittest.TestCase):
    def test_default_triggers_cover_core_and_body_fields(self):
        self.assertEqual(
            pipeline.missing_triggers({}, pipeline.DEFAULT_TRIGGERS),
            ["price", "district", "nickname", "body"],
        )
        fields = {"price_amount": 800.0, "nickname": "小甜", "weight_kg": 45.0}
        self.assertEqual(
            pipeline.missing_triggers(fields, pipeline.DEFAULT_TRIGGERS),
            ["district"],
        )

    def test_explicit_rule_triggers_are_respected(self):
        self.assertEqual(
            pipeline.missing_triggers({"nickname": "小甜"}, ["price"]),
            ["price"],
        )
        self.assertEqual(
            pipeline.missing_triggers({"nickname": "小甜"}, ["nickname"]),
            [],
        )


if __name__ == "__main__":
    unittest.main()
