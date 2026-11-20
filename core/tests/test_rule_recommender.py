import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api import rules as rules_api
from app.extractor import ast_rules, rule_recommender


class RuleRecommenderTests(unittest.TestCase):
    def test_clusters_repeated_uncovered_terms_and_keeps_three_examples(self):
        rows = [
            {"listing_id": i, "field_name": "price_1p_text", "old_value": "未识别",
             "new_value": "600", "raw_snippet": "火箭套餐 6章40分钟", "nickname": f"卡{i}"}
            for i in range(1, 5)
        ]
        candidates = rule_recommender.cluster_corrections(rows, covered={"套餐"})
        candidate = next(item for item in candidates if item["raw_pattern"] == "火箭")
        self.assertEqual(candidate["target_action"], "1p")
        self.assertEqual(candidate["frequency"], 4)
        self.assertEqual(len(candidate["examples"]), 3)

    def test_filters_covered_low_frequency_and_dismissed_candidates(self):
        rows = [
            {"field_name": "bust_size", "new_value": "C", "raw_snippet": "小众系C杯"},
            {"field_name": "bust_size", "new_value": "C", "raw_snippet": "小众系C杯"},
            {"field_name": "bust_size", "new_value": "C", "raw_snippet": "已登记词C杯"},
            {"field_name": "age_years", "new_value": "19", "raw_snippet": "芳龄19"},
        ]
        candidates = rule_recommender.cluster_corrections(rows, covered={"已登记词"})
        self.assertFalse(any(item["raw_pattern"] == "已登记词" for item in candidates))
        self.assertFalse(any(item["field_name"] == "age_years" for item in candidates))
        bust = next(item for item in candidates if item["field_name"] == "bust_size")
        self.assertEqual(bust["target_action"], "胸围C")
        dismissed = rule_recommender.cluster_corrections(rows, dismissed={bust["candidate_key"]})
        self.assertFalse(dismissed)

    def test_weight_alias_is_applied_to_extraction_without_release(self):
        ast_rules.apply_dynamic_terms([
            {"category": "weight_alias", "source_term": "轻盈", "target_action": "体重55kg"},
        ])
        self.assertEqual(ast_rules.find_weight("轻盈"), ("体重55kg", 55.0, 55.0))
        ast_rules.clear_dynamic_terms()


class RuleCandidateAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_backtest_counts_per_sample_improvement_regression_and_no_change(self):
        class Pool:
            async def fetch(self, sql, *args):
                return []

        baseline = {"results": [
            {"raw_id": 1, "differences": {"bust": {}}},
            {"raw_id": 2, "differences": {}},
            {"raw_id": 3, "differences": {"age": {}}},
        ]}
        proposed = {"results": [
            {"raw_id": 1, "differences": {}},
            {"raw_id": 2, "differences": {"weight": {}}},
            {"raw_id": 3, "differences": {"age": {}}},
        ]}
        candidate = {"raw_pattern": "新词", "category": "bust_alias", "target_action": "胸围C"}
        with patch.object(rules_api.slang_registry, "ensure_fresh", new=AsyncMock()), \
                patch.object(rules_api, "dry_run", new=AsyncMock(side_effect=[baseline, proposed])):
            result = await rules_api._candidate_backtest(Pool(), candidate)
        self.assertEqual(result, {"total": 3, "improved": 1, "broken": 1, "unchanged": 1})

    async def test_adoption_refuses_golden_sample_regression(self):
        candidate = {"candidate_key": "abcd12345678", "raw_pattern": "火箭",
                     "category": "tariff_alias", "target_action": "1p"}
        with patch.object(rules_api, "_pool", return_value=object()), \
                patch.object(rules_api, "_recommendation_candidates", new=AsyncMock(return_value=[candidate])), \
                patch.object(rules_api, "_candidate_backtest", new=AsyncMock(return_value={
                    "total": 2, "improved": 0, "broken": 1, "unchanged": 1,
                })):
            with self.assertRaises(HTTPException) as raised:
                await rules_api.adopt_candidate(rules_api.CandidateActionBody(candidate_key="abcd12345678"))
        self.assertEqual(raised.exception.status_code, 409)

    async def test_adoption_persists_and_hot_reloads_candidate(self):
        candidate = {"candidate_key": "abcd12345678", "raw_pattern": "火箭",
                     "category": "tariff_alias", "target_action": "1p"}

        class Pool:
            async def fetch(self, sql, *args):
                return [{"id": 12, "category": "tariff_alias", "source_term": "火箭",
                         "target_action": "1p", "factor": None, "enabled": True}]

        pool = Pool()
        ast_rules.clear_dynamic_terms()
        with patch.object(rules_api, "_pool", return_value=pool), \
                patch.object(rules_api, "_recommendation_candidates", new=AsyncMock(return_value=[candidate])), \
                patch.object(rules_api, "_candidate_backtest", new=AsyncMock(return_value={
                    "total": 2, "improved": 1, "broken": 0, "unchanged": 1,
                })), \
                patch.object(rules_api, "SQL_SLANG_UPSERT", "upsert"), \
                patch.object(rules_api.slang_registry, "SQL_ENABLED", "enabled"):
            response = await rules_api.adopt_candidate(rules_api.CandidateActionBody(candidate_key="abcd12345678"))
        self.assertEqual(response["applied_terms"], 1)
        self.assertEqual(ast_rules.normalize_input("火箭600"), "1p600")
        ast_rules.clear_dynamic_terms()


if __name__ == "__main__":
    unittest.main()
