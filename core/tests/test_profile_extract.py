import asyncio
import json
import unittest

from app.extractor import profile_extract as pe


class LearnPromptTests(unittest.TestCase):
    def test_samples_are_numbered_into_user_message(self):
        messages = pe.build_learn_messages(["课费：1P800", "价格 8张"])
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("[1] 课费：1P800", messages[1]["content"])
        self.assertIn("[2] 价格 8张", messages[1]["content"])

    def test_extract_prompt_carries_profile_context(self):
        profile = {
            "style_notes": "课费+1P写法",
            "price_patterns": ["1P800", "8张"],
            "field_markers": {"价格": "课费："},
            "few_shot": [{"text": "课费：1P800", "extracted": {"price_amount": 800}}],
        }
        messages = pe.build_extract_messages("艺名：小甜\n课费：1P800", profile)
        user = messages[1]["content"]
        self.assertIn("课费+1P写法", user)
        self.assertIn("1P800、8张", user)
        self.assertIn("艺名：小甜", user)


class ParseLlmJsonTests(unittest.TestCase):
    def test_plain_fenced_and_wrapped_json(self):
        self.assertEqual(pe.parse_llm_json('{"a": 1}'), {"a": 1})
        self.assertEqual(pe.parse_llm_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(pe.parse_llm_json('结果如下：{"a": 1} 请查收'), {"a": 1})

    def test_invalid_or_non_dict_returns_none(self):
        self.assertIsNone(pe.parse_llm_json("not json at all"))
        self.assertIsNone(pe.parse_llm_json("[1, 2]"))
        self.assertIsNone(pe.parse_llm_json(""))
        self.assertIsNone(pe.parse_llm_json(None))


class CoerceTests(unittest.TestCase):
    def test_price_string_and_invalid_values(self):
        clean = pe.coerce_parsed({
            "price_amount": "800", "age_years": "22", "height_cm": 168,
            "weight_kg": "48.5", "bust_size": "C", "nickname": "小甜",
            "price_currency": "USD",
        })
        self.assertEqual(clean["price_amount"], 800.0)
        self.assertEqual(clean["age_years"], 22)
        self.assertEqual(clean["height_cm"], 168)
        self.assertEqual(clean["weight_kg"], 48.5)
        self.assertEqual(clean["bust_size"], "C")
        self.assertEqual(clean["price_currency"], "USD")

    def test_garbage_degrades_to_none_and_lists_are_capped(self):
        clean = pe.coerce_parsed({
            "price_amount": "八百", "tariffs": {"p1": [f"价{i}" for i in range(20)]},
            "services": ["a", " ", 123],
        })
        self.assertIsNone(clean["price_amount"])
        self.assertLessEqual(len(clean["tariffs"]["p1"]), 6)
        self.assertEqual(clean["services"], ["a", "123"])

    def test_has_content(self):
        self.assertFalse(pe.has_content({}))
        self.assertFalse(pe.has_content({"nickname": None, "tariffs": {}}))
        self.assertTrue(pe.has_content({"district": "浦东"}))
        self.assertTrue(pe.has_content({"tariffs": {"p1": ["1P800"]}}))


class MergeTests(unittest.TestCase):
    BASE_FIELDS = {"nickname": "小甜", "price_amount": 700.0}
    BASE_EVIDENCE = {"nickname": {"value": "小甜", "src": "regex"}}

    def test_llm_fills_missing_and_never_overwrites(self):
        parsed = pe.coerce_parsed({
            "nickname": "其他", "price_amount": 999, "district": "浦东",
            "tariffs": {"p1": ["1P800 2P1500"]},
            "services": ["陪浴"], "restrictions": ["禁无套"],
        })
        fields, evidence, filled = pe.merge_profile_result(
            dict(self.BASE_FIELDS), dict(self.BASE_EVIDENCE), parsed,
        )
        self.assertEqual(fields["nickname"], "小甜")      # regex wins
        self.assertEqual(fields["price_amount"], 700.0)   # regex wins
        self.assertEqual(fields["district"], "浦东")       # llm filled
        self.assertEqual(evidence["district"]["src"], "llm")
        self.assertEqual(evidence["price_1p_text"]["value"], ["1P800 2P1500"])
        self.assertEqual(evidence["restriction_text"]["value"], ["禁无套"])
        self.assertIn("district", filled)

    def test_corroboration_recorded_without_value_change(self):
        parsed = pe.coerce_parsed({"nickname": "小甜"})
        fields, evidence, filled = pe.merge_profile_result(
            dict(self.BASE_FIELDS), dict(self.BASE_EVIDENCE), parsed,
        )
        self.assertEqual(fields["nickname"], "小甜")
        self.assertEqual(evidence["nickname"]["src"], "regex")  # 原证据保留
        self.assertEqual(evidence["nickname"]["llm_corroboration"], "小甜")
        self.assertEqual(filled, [])

    def test_profile_ready_gate(self):
        self.assertFalse(pe.profile_ready(None, {"base_url": "x", "model": "m"}))
        self.assertFalse(pe.profile_ready({"a": 1}, {"base_url": "", "model": "m"}))
        self.assertTrue(pe.profile_ready({"a": 1}, {"base_url": "x", "model": "m"}))


class LearnFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_too_few_samples_raises_friendly_error(self):
        pool = FakePool(rows=[{"raw_text": "短样本"} for _ in range(2)])
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(pe.learn_channel_profile(pool, lambda *a: "", {}, 5))
        self.assertIn("样本不足", str(ctx.exception))

    def test_learn_parses_fenced_json_and_persists(self):
        answer = '```json\n{"version": 1, "style_notes": "1P 写法", "few_shot": [{"text": "课费 1P800"}]}\n```'
        pool = FakePool(rows=[{"raw_text": f"样本{i}：课费 1P800"} for i in range(10)])
        profile = asyncio.run(pe.learn_channel_profile(pool, lambda *a: answer, {}, 5))
        self.assertEqual(profile["style_notes"], "1P 写法")
        self.assertEqual(profile["sample_count"], 10)
        self.assertEqual(len(pool.updates), 1)
        stored = json.loads(pool.updates[0][1])
        self.assertEqual(stored["version"], 1)

    def test_unparsable_answer_raises_friendly_error(self):
        pool = FakePool(rows=[{"raw_text": f"样本{i}"} for i in range(10)])
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(pe.learn_channel_profile(pool, lambda *a: "我说不上来", {}, 5))
        self.assertIn("无法解析", str(ctx.exception))


class ExtractFlowTests(unittest.IsolatedAsyncioTestCase):
    PROFILE = {"style_notes": "课费 1P 写法", "few_shot": []}

    async def test_extract_returns_coerced_schema(self):
        def fake_chat(messages, timeout=60):
            return '```json\n{"district": "浦东", "price_amount": 800, ' \
                   '"tariffs": {"p1": ["1P800"]}, "restrictions": ["禁无套"]}\n```'
        parsed = await pe.extract_with_profile(fake_chat, "课费 1P800", self.PROFILE)
        self.assertEqual(parsed["district"], "浦东")
        self.assertEqual(parsed["price_amount"], 800.0)
        self.assertEqual(parsed["tariffs"]["p1"], ["1P800"])

    async def test_model_failure_degrades_to_none(self):
        def broken_chat(messages, timeout=60):
            raise TimeoutError("endpoint down")
        self.assertIsNone(await pe.extract_with_profile(broken_chat, "x", self.PROFILE))

    async def test_empty_answer_degrades_to_none(self):
        self.assertIsNone(await pe.extract_with_profile(lambda *a: "", "x", self.PROFILE))
        self.assertIsNone(await pe.extract_with_profile(
            lambda *a: '{"nickname": null}', "x", self.PROFILE,
        ))


class FakePool:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []

    async def fetch(self, sql, *args):
        if "$2::jsonb" in sql or "UPDATE ingest_sources" in sql:
            self.updates.append(args)
            return [{"id": args[0]}]
        return self._rows


if __name__ == "__main__":
    unittest.main()
