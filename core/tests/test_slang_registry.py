import asyncio
import json
import unittest

from app.api import rules as rules_api
from app.extractor import ast_rules, slang_registry
from app.extractor.correction_diffs import compute_diffs


class SlangHotReloadTests(unittest.TestCase):
    """动态词表增删改后 normalize_input / 金额折算即时生效。"""

    def setUp(self):
        ast_rules.clear_dynamic_terms()

    def tearDown(self):
        ast_rules.clear_dynamic_terms()

    def test_money_slang_takes_effect_immediately(self):
        self.assertEqual(ast_rules.normalize_input("8斗"), "8斗")  # 未登记前不折算
        applied = ast_rules.apply_dynamic_terms([
            {"category": "money_slang", "source_term": "斗", "factor": 100},
        ])
        self.assertEqual(applied, 1)
        self.assertEqual(ast_rules.normalize_input("8斗"), "800")
        ast_rules.clear_dynamic_terms()
        self.assertEqual(ast_rules.normalize_input("8斗"), "8斗")  # 禁用即失效

    def test_dynamic_term_overrides_builtin_factor(self):
        ast_rules.apply_dynamic_terms([
            {"category": "money_slang", "source_term": "章", "factor": 1000},
        ])
        self.assertEqual(ast_rules.normalize_input("6章"), "6000")  # 动态覆盖内置 100

    def test_tariff_alias_folds_before_extraction(self):
        ast_rules.apply_dynamic_terms([
            {"category": "tariff_alias", "source_term": "一次", "target_action": "1p"},
            {"category": "tariff_alias", "source_term": "双次", "target_action": "2p"},
        ])
        self.assertEqual(ast_rules.normalize_input("一次500 双次900"), "1p500 2p900")
        # 折算后的文本可直接被阶梯资费解析器消费
        from app.extractor.ocr_fields import tariff_quotes

        quotes = tariff_quotes(["一次500 双次900"])
        self.assertEqual(quotes["1p"]["amount"], 500.0)
        self.assertEqual(quotes["2p"]["amount"], 900.0)

    def test_bust_and_age_aliases_fold_to_canonical_markers(self):
        ast_rules.apply_dynamic_terms([
            {"category": "bust_alias", "source_term": "🐻", "target_action": "胸围"},
            {"category": "bust_alias", "source_term": "熊", "target_action": "胸围"},
            {"category": "age_alias", "source_term": "芳龄", "target_action": "年龄"},
        ])
        self.assertEqual(ast_rules.normalize_input("🐻C 熊D"), "胸围C 胸围D")
        self.assertEqual(ast_rules.normalize_input("芳龄19"), "年龄19")

    def test_latin_alias_terms_have_word_guards(self):
        ast_rules.apply_dynamic_terms([
            {"category": "tariff_alias", "source_term": "cup", "target_action": "2p"},
        ])
        # 600cup 内部的 cup 不会被切碎
        self.assertEqual(ast_rules.normalize_input("600cup"), "600cup")
        self.assertEqual(ast_rules.normalize_input("cup 800"), "2p 800")


class CorrectionDiffTests(unittest.TestCase):
    def test_field_level_diffs_match_task_examples(self):
        diffs = compute_diffs(
            {"weight_kg": 110},
            {"weight_kg": 55.0, "age_years": 19},
            evidence={"weight_kg": {"line": "体重:110", "score": 0.9}},
        )
        by_field = {diff["field_name"]: diff for diff in diffs}
        self.assertEqual(by_field["weight_kg"]["old_value"], "110")
        self.assertEqual(by_field["weight_kg"]["new_value"], "55.0")
        self.assertEqual(by_field["weight_kg"]["raw_snippet"], "体重:110")
        self.assertEqual(by_field["age_years"]["old_value"], "null")
        self.assertEqual(by_field["age_years"]["new_value"], "19")

    def test_base_fields_and_no_change_produce_nothing(self):
        self.assertEqual(compute_diffs({}, {}, base_before={}, base_after={}), [])
        diffs = compute_diffs(
            {}, {},
            base_before={"price_amount": 680}, base_after={"price_amount": 680},
        )
        self.assertEqual(diffs, [])
        price = compute_diffs(
            {}, {},
            base_before={"price_amount": None}, base_after={"price_amount": 680},
        )
        self.assertEqual(price[0]["field_name"], "price_amount")

    def test_record_binds_batch_as_single_jsonb_parameter(self):
        class FakePool:
            def __init__(self):
                self.calls = []

            async def fetchval(self, sql, *args):
                self.calls.append((sql, args))
                return len(json.loads(args[1]))

        from app.extractor import correction_diffs

        pool = FakePool()
        diffs = compute_diffs({"weight_kg": 110}, {"weight_kg": 55.0})
        written = asyncio.run(correction_diffs.record(pool, 821, diffs, 42))
        self.assertEqual(written, 1)
        sql, args = pool.calls[0]
        self.assertIn("correction_diffs_record", sql)
        self.assertEqual(args[0], 821)
        self.assertEqual(args[2], 42)


class InsightSuggestionTests(unittest.TestCase):
    def test_tiered_fee_field_suggests_tariff_alias(self):
        suggestions = rules_api.suggest_slang_entries(
            "price_2p_text", "['双次 900']", "2p",
        )
        self.assertEqual(
            suggestions[0],
            {"category": "tariff_alias", "source_term": "双次", "target_action": "2p"},
        )

    def test_non_tiered_fields_and_targets_produce_nothing(self):
        self.assertEqual(rules_api.suggest_slang_entries("weight_kg", "110", "55.0"), [])
        self.assertEqual(
            rules_api.suggest_slang_entries("price_1p_text", "['一次500']", "600 元"),
            [],
        )


class RegistryLoaderTests(unittest.IsolatedAsyncioTestCase):
    class FakePool:
        def __init__(self, rows):
            self.rows = rows
            self.queries = 0

        async def fetch(self, sql):
            self.queries += 1
            return self.rows

    def setUp(self):
        slang_registry.invalidate()
        ast_rules.clear_dynamic_terms()

    def tearDown(self):
        slang_registry.invalidate()
        ast_rules.clear_dynamic_terms()

    async def test_ttl_gates_repeat_queries(self):
        pool = self.FakePool([
            {"category": "money_slang", "source_term": "斗", "target_action": None, "factor": 100},
        ])
        first = await slang_registry.ensure_fresh(pool)
        self.assertEqual(first, 1)
        self.assertEqual(ast_rules.normalize_input("8斗"), "800")
        await slang_registry.ensure_fresh(pool)
        self.assertEqual(pool.queries, 1)  # TTL 内不重复查库

    async def test_reload_forces_refresh_and_invalidates(self):
        pool = self.FakePool([])
        await slang_registry.ensure_fresh(pool)
        count = await slang_registry.reload(pool)
        self.assertEqual(count, 0)
        self.assertEqual(ast_rules.normalize_input("8斗"), "8斗")


    async def test_reload_replaces_aliases_without_restarting_and_discards_disabled_entries(self):
        pool = self.FakePool([{"category": "tariff_alias", "source_term": "体验",
                               "target_action": "1p"}])
        await slang_registry.ensure_fresh(pool)
        self.assertIn("1p", ast_rules.tariff_quotes(["体验600"]))
        pool.rows = [{"category": "tariff_alias", "source_term": "体验", "target_action": "2p"}]
        await slang_registry.reload(pool)
        self.assertEqual(set(ast_rules.tariff_quotes(["体验600"])), {"2p"})
        pool.rows[0]["enabled"] = False
        await slang_registry.reload(pool)
        self.assertFalse(ast_rules.tariff_quotes(["体验600"]))

    async def test_preview_is_isolated_from_a_concurrent_reader(self):
        running = asyncio.Event()
        reader_done = asyncio.Event()
        row = {"category": "money_slang", "source_term": "斗", "factor": 100}
        await slang_registry.reload(self.FakePool([row]))

        async def candidate():
            with ast_rules.preview_dynamic_terms([row | {"factor": 200}]):
                running.set()
                await reader_done.wait()
                self.assertEqual(ast_rules.normalize_input("6斗"), "1200")

        task = asyncio.create_task(candidate())
        await running.wait()
        try:
            self.assertEqual(ast_rules.normalize_input("6斗"), "600")
        finally:
            reader_done.set()
            await task
        self.assertEqual(ast_rules.normalize_input("6斗"), "600")


if __name__ == "__main__":
    unittest.main()
