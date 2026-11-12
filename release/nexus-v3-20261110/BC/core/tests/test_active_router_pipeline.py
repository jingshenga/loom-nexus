"""Active pipeline routing tests; Telegram and OCR are never called."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.extractor import pipeline
from app.extractor.pipeline import _shadow_safe_prediction


class ActiveRouterPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rule = {
            "parser_key": "ast_rules_v1", "gate": {},
            "extract": {"username": [r"@([A-Za-z0-9_]{5,32})"]},
            "ocr": {"enabled": False, "trigger_when_missing": []}, "lifecycle_terms": [],
        }
        self.binding = {
            "rule_id": 7, "rule_version_id": 71, "rule_type": "listing", "is_active": True,
            "is_default": False, "priority": 10, "purpose": "listing", "match_group": "main",
            "mode": "exclusive_first_valid", "config_json": self.rule,
        }
        self.pool = SimpleNamespace(
            fetchval=AsyncMock(return_value="active"),
            fetch=AsyncMock(return_value=[self.binding]),
            fetchrow=AsyncMock(return_value={"id": 4, "content_hash": "safe-hash"}),
            execute=AsyncMock(),
        )
        self.extractor = pipeline.Extractor(self.pool, "", False, ocr_worker=object())
        self.message = {"source_id": 2, "raw_id": 15, "raw_text": "@alphauser",
                        "sender_peer_id": 901}

    def settings(self, **overrides):
        defaults = {"active_router_enabled": True, "active_router_kill_switch": False,
                    "active_router_source_ids": "2", "ocr_review_assist_enabled": False}
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    async def test_mode_and_flags_fail_closed_to_legacy(self):
        with patch.object(pipeline, "get_settings", return_value=self.settings(active_router_enabled=False)):
            route = await self.extractor._active_route(self.message)
        self.assertEqual(route["review_reason"], "active_router_disabled")
        self.pool.fetchval.assert_awaited_once()

    async def test_non_active_source_keeps_legacy_path(self):
        self.pool.fetchval.return_value = "legacy"
        with patch.object(pipeline, "get_settings", return_value=self.settings()):
            self.assertIsNone(await self.extractor._active_route(self.message))

    async def test_six_none_empty_shadow_identity_diffs_are_not_real_differences(self):
        actual = {"fields": {}, "identity": None}
        results = [_shadow_safe_prediction({"selected": None}, actual) for _ in range(6)]
        self.assertEqual(sum(item["identity_diff"] for item in results), 0)

    async def test_allowlisted_active_source_uses_bound_version_and_fields(self):
        with patch.object(pipeline, "get_settings", return_value=self.settings()):
            route = await self.extractor._active_route(self.message)
        self.assertEqual(route["fields"]["username"], "alphauser")
        self.assertEqual(route["rule_config"], self.rule)
        self.assertEqual(route["review_reason"], "human_review_required")
        self.assertTrue(route["result"]["selected"])
        self.pool.fetch.assert_awaited_once_with(pipeline.SQL_SHADOW_BINDINGS, 2)

    async def test_valid_active_candidate_is_queued_without_listing_creation(self):
        route = {"review_reason": "human_review_required", "fields": {"username": "alphauser"},
                 "evidence": {}, "rule_config": self.rule,
                 "result": {"selected": {"fields": {"username": "alphauser"},
                                          "identity": "alphauser", "rule_ids": [7]}, "trace": []}}
        with patch.object(self.extractor, "_active_route", new=AsyncMock(return_value=route)), \
             patch.object(self.extractor, "_enqueue_active_review", new=AsyncMock()) as enqueue, \
             patch.object(self.extractor, "_record_shadow_if_enabled", new=AsyncMock()):
            await self.extractor._process_one({**self.message, "rule_config": self.rule, "media_json": []})
        enqueue.assert_awaited_once()
        self.assertEqual(self.pool.fetch.await_args.args, (pipeline.SQL_MARK, 15, "dropped"))
        self.assertFalse(any(call.args and call.args[0] == pipeline.SQL_CREATE_LISTING
                             for call in self.pool.fetch.await_args_list))

    async def test_conflicting_identity_is_queued_without_raw_text(self):
        second = {**self.binding, "rule_id": 8, "rule_version_id": 81,
                  "match_group": "other", "config_json": {
                      **self.rule, "extract": {"username": [r"另一个TG[:：]([A-Za-z0-9_]{5,32})"]},
                  }}
        self.pool.fetch.return_value = [self.binding, second]
        with patch.object(pipeline, "get_settings", return_value=self.settings()):
            route = await self.extractor._active_route({**self.message,
                                                        "raw_text": "@alphauser 另一个TG:betaguy"})
            await self.extractor._enqueue_active_review(
                {**self.message, "raw_text": "@alphauser 另一个TG:betaguy"}, route,
            )
        self.assertEqual(route["review_reason"], "rule_conflict")
        args = self.pool.execute.await_args.args
        self.assertEqual(args[1:5], (2, 15, "safe-hash", "rule_conflict"))
        self.assertNotIn("alphauser", args[-1])
        self.assertNotIn("betaguy", args[-1])

    async def test_router_exception_is_parked_for_review_not_legacy(self):
        self.pool.fetch.side_effect = RuntimeError("database query failed")
        with patch.object(pipeline, "get_settings", return_value=self.settings()), \
             patch.object(pipeline.logger, "exception"):
            route = await self.extractor._active_route(self.message)
        self.assertEqual(route["review_reason"], "active_router_error")

    async def test_no_rule_match_is_parked_for_review(self):
        with patch.object(pipeline, "get_settings", return_value=self.settings()):
            route = await self.extractor._active_route({**self.message, "raw_text": "普通聊天，没有名片字段"})
        self.assertEqual(route["review_reason"], "no_rule_match")

    async def test_active_lifecycle_match_uses_lifecycle_review_queue(self):
        active = {
            "review_reason": "lifecycle_requires_human_review",
            "result": {"selected": {
                "lifecycle": "已售", "fields": {"username": "alphauser"},
            }},
        }
        with patch.object(self.extractor, "_active_route", new=AsyncMock(return_value=active)), \
             patch.object(self.extractor, "_enqueue_active_review", new=AsyncMock()) as enqueue, \
             patch.object(self.extractor, "_record_shadow_if_enabled", new=AsyncMock()):
            await self.extractor._process_one({
                **self.message, "raw_text": "@alphauser 已售", "rule_config": self.rule,
                "media_json": [],
            })

        self.assertEqual(self.pool.fetch.await_count, 2)
        sql, raw_id, sender_peer_id, term, username, reason, evidence = self.pool.fetch.await_args_list[0].args
        self.assertEqual(sql, pipeline.SQL_LIFECYCLE_REVIEW)
        self.assertEqual((raw_id, sender_peer_id, term, username), (15, 901, "已售", "alphauser"))
        self.assertEqual(reason, "source_or_sender_authority_unverified")
        self.assertIn("sender_authority_verified", evidence)
        enqueue.assert_not_awaited()
        self.assertEqual(self.pool.fetch.await_args_list[1].args, (pipeline.SQL_MARK, 15, "dropped"))


if __name__ == "__main__":
    unittest.main()
