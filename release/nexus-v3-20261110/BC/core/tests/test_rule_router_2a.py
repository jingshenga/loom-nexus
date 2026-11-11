import unittest

from app.extractor.rule_config import validate_match_mode, validate_rule_config
from app.extractor.rule_router import (
    active_source_allowed,
    business_candidate_key,
    legacy_selected,
    resolve_active_listing,
    resolve_group,
    shadow_compare,
)


class RuleRouter2ATests(unittest.TestCase):
    def setUp(self):
        self.rule = {
            "parser_key": "ast_rules_v1",
            "gate": {},
            "extract": {"username": [r"@([A-Za-z0-9_]{5,32})"]},
            "ocr": {"enabled": False, "trigger_when_missing": []},
            "lifecycle_terms": [],
        }

    def binding(self, rule_id, username_regex, **extra):
        config = dict(self.rule)
        config["extract"] = {"username": [username_regex]}
        return {
            "rule_id": rule_id, "rule_version_id": rule_id * 10,
            "rule_type": "listing", "priority": rule_id,
            "config_json": config, "is_active": True,
            "purpose": "listing", "match_group": "default",
            "mode": "exclusive_first_valid", **extra,
        }

    def test_legacy_rule_choice_and_default_fallback(self):
        selected = legacy_selected([
            self.binding(3, r"@([A-Za-z0-9_]{5,32})"),
            {**self.binding(1, r"@([A-Za-z0-9_]{5,32})"), "is_default": True},
        ])
        self.assertEqual(selected["rule_id"], 3)
        selected = legacy_selected([
            {**self.binding(1, r"@([A-Za-z0-9_]{5,32})"), "is_default": True},
        ])
        self.assertEqual(selected["rule_id"], 1)

    def test_active_gate_defaults_closed_requires_allowlist_and_honors_emergency_kill(self):
        self.assertFalse(active_source_allowed(2))
        self.assertFalse(active_source_allowed(2, enabled=True, allowed_source_ids="2"))
        self.assertFalse(active_source_allowed(2, enabled=True, allowed_source_ids="2", kill_switch=True))
        self.assertTrue(active_source_allowed(2, enabled=True, allowed_source_ids="1, 2", kill_switch=False))
        self.assertFalse(active_source_allowed(3, enabled=True, allowed_source_ids="1, 2", kill_switch=False))

    def test_exclusive_checks_all_matches_for_conflicting_tg(self):
        result = resolve_group([
            self.binding(1, r"@([A-Za-z0-9_]{5,32})"),
            self.binding(2, r"另一个TG[:：]([A-Za-z0-9_]{5,32})"),
        ], "@alphauser 另一个TG:betaguy")
        self.assertIsNone(result["selected"])
        self.assertEqual(result["conflicts"][0]["kind"], "telegram_identity")

    def test_fallback_on_no_match_uses_next_candidate(self):
        bindings = [
            self.binding(1, r"不存在[:：]([A-Za-z0-9_]{5,32})", mode="fallback_on_no_match"),
            self.binding(2, r"@([A-Za-z0-9_]{5,32})", mode="fallback_on_no_match"),
        ]
        result = resolve_group(bindings, "@alphauser")
        self.assertEqual(result["selected"]["rule_id"], 2)

    def test_fallback_order_precedes_priority(self):
        bindings = [
            self.binding(1, r"@([A-Za-z0-9_]{5,32})", mode="fallback_on_no_match",
                         priority=99, fallback_order=1),
            self.binding(2, r"@([A-Za-z0-9_]{5,32})", mode="fallback_on_no_match",
                         priority=1, fallback_order=0),
        ]
        result = resolve_group(bindings, "@alphauser")
        self.assertEqual(result["selected"]["rule_id"], 2)

    def test_fallback_on_no_structure_skips_lifecycle_only_match(self):
        lifecycle_only = self.binding(
            1, r"never[:：]([A-Za-z0-9_]{5,32})", mode="fallback_on_no_structure",
            fallback_order=0,
        )
        lifecycle_only["config_json"]["lifecycle_terms"] = ["休息"]
        structured = self.binding(
            2, r"@([A-Za-z0-9_]{5,32})", mode="fallback_on_no_structure",
            fallback_order=1,
        )
        result = resolve_group([lifecycle_only, structured], "休息 @alphauser")
        self.assertEqual(result["selected"]["rule_id"], 2)
        self.assertEqual(result["reason"], "first_structured_match")

    def test_additive_merges_disjoint_fields_and_preserves_provenance(self):
        username = self.binding(1, r"@([A-Za-z0-9_]{5,32})", mode="additive")
        nickname = self.binding(2, r"never[:：]([^ ]+)", mode="additive")
        nickname["config_json"]["extract"] = {"nickname": [r"昵称[:：]([^ ]+)"]}
        result = resolve_group([username, nickname], "昵称：小林 @alphauser")
        self.assertEqual(result["reason"], "additive_merge")
        self.assertEqual(result["selected"]["fields"], {"username": "alphauser", "nickname": "小林"})
        self.assertEqual(result["selected"]["rule_ids"], [2, 1])
        self.assertEqual(result["conflicts"], [])

    def test_additive_detects_different_values_for_the_same_field(self):
        first = self.binding(1, r"never[:：]([^ ]+)", mode="additive")
        first["config_json"]["extract"] = {"nickname": [r"昵称[:：]([^ ]+)"]}
        second = self.binding(2, r"never[:：]([^ ]+)", mode="additive")
        second["config_json"]["extract"] = {"nickname": [r"别名[:：]([^ ]+)"]}
        result = resolve_group([first, second], "昵称：小林 别名：阿青")
        self.assertIsNone(result["selected"])
        self.assertEqual(result["conflicts"][0]["kind"], "field")

    def test_active_keeps_purposes_separate_and_merges_distinct_listing_groups(self):
        username = self.binding(1, r"@([A-Za-z0-9_]{5,32})", mode="additive", match_group="identity")
        nickname = self.binding(2, r"never[:：]([^ ]+)", mode="additive", match_group="profile")
        nickname["config_json"]["extract"] = {"nickname": [r"昵称[:：]([^ ]+)"]}
        other_purpose = self.binding(3, r"@([A-Za-z0-9_]{5,32})", purpose="checkin_reverse")
        result = resolve_active_listing([username, nickname, other_purpose], "昵称：小林 @alphauser")
        self.assertEqual(result["selected"]["identity"], "alphauser")
        self.assertEqual(result["selected"]["fields"], {"username": "alphauser", "nickname": "小林"})
        self.assertEqual(result["selected"]["rule_ids"], [1, 2])
        self.assertEqual({entry["rule_id"] for entry in result["trace"]}, {1, 2})

    def test_active_does_not_choose_between_conflicting_formats(self):
        first = self.binding(1, r"@([A-Za-z0-9_]{5,32})", match_group="first")
        second = self.binding(2, r"另一个TG[:：]([A-Za-z0-9_]{5,32})", match_group="second")
        result = resolve_active_listing([first, second], "@alphauser 另一个TG:betaguy")
        self.assertIsNone(result["selected"])
        self.assertEqual(result["reason"], "candidate_conflict")
        self.assertIn("telegram_identity", [item["kind"] for item in result["conflicts"]])

    def test_active_identity_missing_is_a_candidate_not_an_auto_publish_signal(self):
        nickname = self.binding(1, r"never[:：]([^ ]+)")
        nickname["config_json"]["extract"] = {"nickname": [r"昵称[:：]([^ ]+)"]}
        result = resolve_active_listing([nickname], "昵称：小林")
        self.assertTrue(result["selected"])
        self.assertEqual(result["selected"]["identity"], "")
        self.assertEqual(result["reason"], "active_match")

    def test_all_matching_rules_are_traced_but_identical_identity_is_not_conflict(self):
        bindings = [
            self.binding(1, r"@([A-Za-z0-9_]{5,32})", priority=20),
            self.binding(2, r"@([A-Za-z0-9_]{5,32})", priority=10),
        ]
        result = resolve_group(bindings, "@alphauser")
        self.assertEqual(len(result["trace"]), 2)
        self.assertEqual(result["selected"]["rule_id"], 1)
        self.assertEqual(result["conflicts"], [])

    def test_equal_priority_selection_is_deterministic_by_rule_id(self):
        first = self.binding(2, r"@([A-Za-z0-9_]{5,32})", priority=10)
        second = self.binding(1, r"@([A-Za-z0-9_]{5,32})", priority=10)
        result = resolve_group([first, second], "@alphauser")
        self.assertEqual(result["selected"]["rule_id"], 1)


    def test_shadow_comparison_reports_changes_without_mutating_input(self):
        bindings = [self.binding(1, r"@([A-Za-z0-9_]{5,32})")]
        before = dict(bindings[0])
        result = shadow_compare(
            bindings, "@alphauser",
            legacy_config={"gate": {}, "extract": {}, "ocr": {}, "lifecycle_terms": []},
        )
        self.assertTrue(result["changed"])
        self.assertEqual(bindings[0], before)

    def test_business_key_ignores_rule_version(self):
        key_a = business_candidate_key(10, 20, "listing", "alphauser")
        key_b = business_candidate_key(10, 20, "listing", "ALPHAUSER")
        self.assertEqual(key_a, key_b)

    def test_validator_rejects_unsafe_regex_and_unknown_modes(self):
        dangerous = {**self.rule, "extract": {"username": [r"(a+)+"]}}
        self.assertTrue(validate_rule_config("listing", dangerous))
        self.assertFalse(validate_match_mode("execute_python"))

    def test_secret_bearing_rule_configuration_is_rejected(self):
        secret = {**self.rule, "telegram": {"session_file": "secret.session"}}
        self.assertTrue(validate_rule_config("listing", secret))
        result = resolve_group([self.binding(1, r"@([A-Za-z0-9_]{5,32})", config_json=secret)], "@alphauser")
        self.assertEqual(result["trace"][0]["reason"], "unsafe_or_invalid_config")

    def test_legacy_config_remains_diagnostic_not_strictly_rejected(self):
        self.assertEqual(validate_rule_config("listing", {"extract": {}}, strict=False), [])

    def test_bot_rule_allows_only_the_registered_parser_key(self):
        self.assertEqual(validate_rule_config("bot_collection", {"parser_key": "bot_collection_v1"}), [])
        self.assertTrue(validate_rule_config("bot_collection", {"parser_key": "bot_collection_v1", "script": "x"}))


if __name__ == "__main__":
    unittest.main()
