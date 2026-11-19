"""Regression samples transcribed from the ten supplied OCR pattern groups."""
import pytest

from app.api.listings import build_attributes
from app.extractor import ast_rules, media_ocr, ocr_fields


@pytest.mark.parametrize(("line", "tier", "amount", "minutes"), [
    ("400bt (40分钟)", "bt", 400, 40),
    ("BT 4张 50分钟", "bt", 400, 50),
    ("500p(40分钟)", "1p", 500, 40),
    ("600P (45 min)", "1p", 600, 45),
    ("600/p (40min)", "1p", 600, 40),
    ("一P 6章40分钟", "1p", 600, 40),
    ("900pp(80分钟)", "2p", 900, 80),
    ("1200PP (90min)", "2p", 1200, 90),
    ("二P 11张90分", "2p", 1100, 90),
    ("双次 11k 90分", "2p", 1100, 90),
])
def test_ocr_tariff_samples_follow_the_same_text_and_api_rules(line, tier, amount, minutes):
    expected = f"{amount} 元 ({minutes}分钟)"
    card, card_evidence = media_ocr.extract_card_fields([{"text": line, "score": 0.92}])
    ocr, evidence = ocr_fields.card_to_listing_fields(card, card_evidence, 0.92)
    text, text_evidence = ast_rules.extract_fields({}, line)
    for fields in (ocr, text):
        assert fields[f"price_{tier}"] == amount
        assert fields[f"price_{tier}_display"] == expected
        assert sum(key in fields for key in ("price_1p", "price_2p", "price_bt")) == 1
    assert evidence[f"price_{tier}"]["line"] == line
    api_key = {"1p": "p1", "2p": "p2", "bt": "bt"}[tier]
    assert build_attributes({}, card)["tariffs"][api_key] == [expected]
    assert build_attributes(text_evidence, {})["tariffs"][api_key] == [expected]


def test_mixed_tiers_and_duration_do_not_leak_into_other_quotes():
    quotes = ast_rules.tariff_quotes(["400bt (40分钟) 600/p (45 min) 二P 900 90分"])
    assert {key: quote["minutes"] for key, quote in quotes.items()} == {"bt": 40, "1p": 45, "2p": 90}
    assert ast_rules.normalize_input("价格 0.8k / 11k / 6张 / 6 章") == "价格 800 / 1100 / 600 / 600"
    assert not ast_rules.tariff_quotes(["1P3张照片", "BT 40分钟"])


def test_additional_fees_and_complete_restrictions_survive_listing_cleaning():
    fees = ["任意两小时1200", "三小时1800", "包夜2300"]
    restrictions = ["不闲聊无口嗨", "不做无套", "不接受20岁以下", "不催赶", "不做口", "不亲吻"]
    lines = fees + ["、".join(restrictions)]
    card, _ = media_ocr.extract_card_fields([{"text": line} for line in lines])
    _, text_evidence = ast_rules.extract_fields({}, "\n".join(lines))
    for attributes in (build_attributes({}, card), build_attributes(text_evidence, {})):
        assert attributes["tariffs"]["raw_fees"] == ["任意两小时 1200 元", "三小时 1800 元", "包夜 2300 元"]
        assert attributes["restrictions"] == restrictions
        assert attributes["profile"]["age"] is None


@pytest.mark.parametrize(("line", "key", "expected"), [
    ("B36", "bust_size", "B"), ("36C", "bust_size", "C"), ("C杯", "bust_size", "C"),
    ("大熊D", "bust_size", "D"), ("胸围：B36", "bust_size", "B"),
    ("体重: 98", "weight_kg", 49.0), ("体重: 110", "weight_kg", 55.0),
    ("体重: 48kg", "weight_kg", 48.0), ("体重: 51kg", "weight_kg", 51.0),
    ("23岁", "age_years", 23), ("年龄: 27", "age_years", 27), ("07年", "age_years", 19),
])
def test_body_samples_preserve_raw_line_provenance(line, key, expected):
    text, evidence = ast_rules.extract_fields({}, line)
    card, ocr = media_ocr.extract_card_fields([{"text": line, "score": 0.93}])
    assert text[key] == card[key] == expected
    assert evidence[key]["line"] == line
    assert ocr[key] == {"line": line, "score": 0.93}


def test_dynamic_aliases_and_money_keep_raw_line_and_built_in_fallback():
    ast_rules.apply_dynamic_terms([
        {"category": "tariff_alias", "source_term": "体验档", "target_action": "1p"},
        {"category": "money_slang", "source_term": "斗", "factor": 100},
        {"category": "bust_alias", "source_term": "杯型", "target_action": "胸围"},
        {"category": "age_alias", "source_term": "芳龄", "target_action": "年龄"},
    ])
    try:
        lines = ["体验档6斗40分钟", "杯型B36", "芳龄27"]
        card, _ = media_ocr.extract_card_fields([{"text": line} for line in lines])
        fields, evidence = ast_rules.extract_fields({}, "\n".join(lines))
        assert fields["price_1p_display"] == "600 元 (40分钟)"
        assert evidence["price_1p"]["line"] == lines[0]
        assert card["age_years"] == 27
        assert card["bust_size"] == "B"
    finally:
        ast_rules.clear_dynamic_terms()
    assert not ast_rules.tariff_quotes(["体验档6斗40分钟"])
