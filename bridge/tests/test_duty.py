import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runner.duty import (
    business_window,
    classified_profile_usernames,
    linked_posts,
    listing_contact_username,
    message_matches,
    process_message,
    profile_usernames,
)
from runner.duty_collection import (
    _collect_checkin_source, _find_checkin_matches, _flood_wait_seconds, _is_flood_wait,
    _profile_links, _profile_media_kind,
)


class FakeMessage:
    def __init__(self, text, sender_id, urls=()):
        self.raw_text = text
        self.sender_id = sender_id
        self._urls = urls

    def get_entities_text(self):
        return [(SimpleNamespace(url=url), "link") for url in self._urls]


class LinkedProfileMessage:
    def __init__(self, rows, header=""):
        self.raw_text = "\n".join(([header] if header else []) + [text for text, _ in rows])
        self._entities = []
        offset = len(header.encode("utf-16-le")) // 2 + (1 if header else 0)
        for text, url in rows:
            name = text.rsplit("\u2060", 1)[-1]
            start = text.index(name)
            prefix = text[:start]
            entity = SimpleNamespace(
                url=url,
                offset=offset + len(prefix.encode("utf-16-le")) // 2,
                length=len(name.encode("utf-16-le")) // 2,
            )
            self._entities.append((entity, name))
            offset += len(text.encode("utf-16-le")) // 2 + 1

    def get_entities_text(self):
        return self._entities


class DutyProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_lookup_records_multiple_candidates_without_selecting_owner(self):
        from unittest.mock import AsyncMock
        from runner import duty_collection

        pool = SimpleNamespace(execute=AsyncMock())
        job = {"job_id": 4, "account_id": 2, "contact_username": "fixture_contact"}
        user = SimpleNamespace(bot=False, username="fixture_contact")
        entities = [SimpleNamespace(bot=True, username="fixture_first_bot"),
                    SimpleNamespace(bot=True, username="fixture_second_bot")]
        with patch.object(duty_collection, "_rate_limited_entity", AsyncMock(side_effect=entities)):
            status, reason = await duty_collection._inspect_profile_bots(
                pool, object(), job, user, "Bot: @fixture_first_bot @fixture_second_bot",
            )
        self.assertEqual((status, reason), ("needs_review", "multiple_bots"))
        self.assertEqual(pool.execute.await_args.args[-2], "")
        self.assertEqual(len(json.loads(pool.execute.await_args.args[-1])["candidate_urls"]), 2)

    async def test_checkin_reuses_complete_card_before_telegram_and_audits_ids(self):
        from unittest.mock import AsyncMock
        from runner import duty_collection

        pool = SimpleNamespace(
            fetch=AsyncMock(return_value=[{"plan": json.dumps({
                "decision": "reuse", "reason": "complete_published", "listing_id": 7,
                "local_post": {"id": 11, "revision_id": 14, "raw_text": "fixture private text"},
            })}]),
            execute=AsyncMock(),
        )
        job = {"job_id": 3, "source_id": 2, "target_msg_id": 19, "target_peer_id": -1002,
               "contact_username": "fixture_user", "account_id": 1}
        with patch.object(duty_collection, "_rate_limited_entity", AsyncMock()) as telegram:
            status, _ = await _collect_checkin_source(pool, object(), job, {"source_username": "source"})
        self.assertEqual(status, "skipped")
        telegram.assert_not_awaited()
        audit = json.loads(pool.execute.await_args.args[2])
        self.assertEqual(audit["original"], {"id": 11, "revision_id": 14})
        self.assertEqual(audit["listing_id"], 7)

    async def test_checkin_blocks_identity_conflict_before_telegram(self):
        from unittest.mock import AsyncMock
        from runner import duty_collection

        pool = SimpleNamespace(
            fetch=AsyncMock(return_value=[{"plan": {"decision": "review", "reason": "identity_conflict"}}]),
            execute=AsyncMock(),
        )
        job = {"job_id": 3, "source_id": 2, "target_msg_id": None, "target_peer_id": None,
               "contact_username": "fixture_user", "account_id": 1}
        with patch.object(duty_collection, "_rate_limited_entity", AsyncMock()) as telegram:
            status, _ = await _collect_checkin_source(pool, object(), job, {"source_username": "source"})
        self.assertEqual(status, "needs_review")
        telegram.assert_not_awaited()

    async def test_checkin_passes_local_revision_to_shared_staging_without_telegram(self):
        from unittest.mock import AsyncMock
        from runner import duty_collection

        local_post = {"platform_msg_id": 19, "revision_id": 23, "raw_text": "TG: @fixture_user"}
        pool = SimpleNamespace(
            fetch=AsyncMock(side_effect=[
                [{"plan": {"decision": "supplement", "reason": "local_post_available", "source_peer_id": -1002,
                           "local_post": local_post}}],
                [{"result": {"status": "fetched", "reason": "candidate_created"}}],
            ]),
            execute=AsyncMock(),
        )
        job = {"job_id": 3, "source_id": 2, "target_msg_id": 19, "target_peer_id": -1002,
               "contact_username": "fixture_user", "account_id": 1}
        with patch.object(duty_collection, "_rate_limited_entity", AsyncMock()) as telegram:
            self.assertEqual(await _collect_checkin_source(pool, object(), job, {"source_username": "source"}),
                             ("fetched", None))
        telegram.assert_not_awaited()
        self.assertEqual(pool.fetch.await_args.args[-1], 23)

    async def test_same_nickname_does_not_skip_exact_checkin_lookup(self):
        from unittest.mock import AsyncMock
        from runner import duty_collection

        class Pool:
            def __init__(self):
                self.sql = []

            async def fetch(self, query, *args):
                self.sql.append(query)
                return []

        pool = Pool()
        job = {
            "target_msg_id": None, "target_peer_id": None, "source_id": 1,
            "contact_username": "contact_1", "contact_nickname": "同名老师",
            "account_id": 2,
        }
        with patch.object(duty_collection, "_rate_limited_entity", AsyncMock(return_value=object())), \
             patch.object(duty_collection, "_find_checkin_matches", AsyncMock(return_value=[])):
            status, error = await _collect_checkin_source(
                pool, object(), job, {"source_username": "checkin_source"},
            )
        self.assertEqual(status, "not_found")
        self.assertIn("没有找到", error)
        self.assertNotIn(duty_collection.SQL_CHECKIN_INDEX_NICKNAME, pool.sql)

    async def test_green_only_is_not_enqueued_but_green_blue_is(self):
        message = LinkedProfileMessage([
            ("🟢\u2060团子", "https://t.me/tuanzi?profile"),
            ("🟢\u2060💙\u2060团团子", "https://t.me/tuantuanzi?profile"),
        ], header="💖今日开课老师")
        message.sender_id = 77
        message.date = datetime.now(timezone.utc)
        message.edit_date = None
        message.id = 123

        class Pool:
            def __init__(self):
                self.enqueued = []
                self.snapshot_args = None

            async def fetch(self, sql, *args):
                if sql == "snapshot-sql":
                    self.snapshot_args = args
                    return [{"changed": True}]
                self.enqueued.append(args)
                return []

        pool = Pool()
        rule = {
            "rule_id": 4,
            "source_id": 5,
            "config_json": {
                "sender_peer_id": 77,
                "match_prefix": "💖今日开课老师",
                "identity_mode": "direct_username_links",
                "profile_query_required": True,
                "duty_identity_markers": True,
                "collection_source_id": 8,
            },
        }
        with patch("runner.duty.SQL_SNAPSHOT", "snapshot-sql"):
            await process_message(pool, object(), rule, message)

        self.assertEqual(len(pool.enqueued), 1)
        self.assertEqual(pool.enqueued[0][2], "tuantuanzi")
        self.assertEqual(json.loads(pool.snapshot_args[7]), ["tuanzi"])
        self.assertEqual(json.loads(pool.snapshot_args[8]), ["tuantuanzi"])


class DutyRuleTests(unittest.TestCase):
    def test_bot_lookup_includes_bare_bot_suffix_candidates(self):
        candidates = _profile_links(
            "Bot地址 xiaomubot\n备用 @service_bot\nhttps://t.me/shopbot"
        )
        self.assertIn(("xiaomubot", None), candidates)
        self.assertIn(("service_bot", None), candidates)
        self.assertIn(("shopbot", None), candidates)

    def test_business_day_rolls_at_four(self):
        zone = ZoneInfo("Asia/Shanghai")
        before = datetime(2026, 10, 28, 3, 59, tzinfo=zone)
        after = datetime(2026, 10, 28, 4, 0, tzinfo=zone)
        before_day, before_expiry = business_window(before)
        after_day, _ = business_window(after)
        self.assertEqual(str(before_day), "2026-10-27")
        self.assertEqual(before_expiry, after)
        self.assertEqual(str(after_day), "2026-10-28")

    def test_sender_and_prefix_are_both_required(self):
        config = {"sender_peer_id": 8758449377, "match_prefix": "🔶 在线老师"}
        self.assertTrue(message_matches(FakeMessage("🔶 在线老师，按地区展示", 8758449377), config))
        self.assertFalse(message_matches(FakeMessage("🔶 在线老师，按地区展示", 1), config))
        self.assertFalse(message_matches(FakeMessage("抽奖活动", 8758449377), config))

    def test_tianyi_only_accepts_profile_links(self):
        message = FakeMessage("榜单", 1, [
            "https://t.me/tyssbd_bot?start=update_profile",
            "https://t.me/lemony88?&profile",
            "https://t.me/nbtyss/2092051",
        ])
        usernames = profile_usernames(message, {
            "profile_query_required": True,
            "exclude_usernames": ["tyssbd_bot"],
        })
        self.assertEqual(usernames, ["lemony88"])

    def test_tianyi_skips_only_green_and_collects_green_blue(self):
        message = LinkedProfileMessage([
            ("🟢\u2060团子", "https://t.me/tuanzi?profile"),
            ("🟢\u2060💙\u2060团团子", "https://t.me/tuantuanzi?profile"),
            ("\u2060小号", "https://t.me/othername?profile"),
        ])
        usernames, green_only, green_blue = classified_profile_usernames(message, {
            "profile_query_required": True,
        })
        self.assertEqual(usernames, ["othername", "tuantuanzi"])
        self.assertEqual(green_only, ["tuanzi"])
        self.assertEqual(green_blue, ["tuantuanzi"])

    def test_xiaomuwu_keeps_only_target_channel_posts(self):
        message = FakeMessage("在线老师", 1, [
            "https://t.me/NBXMWBGJ/5153",
            "https://t.me/another/99",
        ])
        posts = linked_posts(message, "NBXMWBGJ")
        self.assertEqual(posts[0][0:2], ("NBXMWBGJ", 5153))
        self.assertEqual(len(posts), 1)

    def test_xiaomuwu_uses_contact_field_not_channel_link(self):
        message = FakeMessage(
            "💌 电报：@waneannb7025\n📮 频道：https://t.me/shiwanwanyanb",
            1,
        )
        self.assertEqual(listing_contact_username(message), "waneannb7025")

    def test_flood_wait_is_deferred_for_telegram_retry(self):
        flood_wait = type("FloodWaitError", (Exception,), {"seconds": 4120})()
        other_error = RuntimeError("invalid username")
        self.assertTrue(_is_flood_wait(flood_wait))
        self.assertEqual(_flood_wait_seconds(flood_wait), 4120)
        self.assertFalse(_is_flood_wait(other_error))

    def test_video_mime_wins_over_photo_attribute_for_profile_trial(self):
        message = SimpleNamespace(
            photo=object(), video=None, video_note=None,
            file=SimpleNamespace(mime_type="video/mp4", ext=".mp4"),
        )
        self.assertEqual(_profile_media_kind(message), "video")

    def test_video_document_wins_over_photo_file_metadata(self):
        message = SimpleNamespace(
            photo=object(), video=None, video_note=None, media=None,
            document=SimpleNamespace(mime_type="video/mp4", attributes=[]),
            file=SimpleNamespace(mime_type="image/jpeg", ext=".jpg"),
        )
        self.assertEqual(_profile_media_kind(message), "video")

    def test_photo_with_video_sizes_is_classified_as_video(self):
        message = SimpleNamespace(
            photo=SimpleNamespace(video_sizes=[object()]),
            video=None, video_note=None, media=None, document=None,
            file=SimpleNamespace(mime_type="image/jpeg", ext=".jpg"),
        )
        self.assertEqual(_profile_media_kind(message), "video")


class CheckinSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_fallback_finds_exact_username_missed_by_search(self):
        hit = FakeMessage("签到资料 @lemony88", sender_id=1)

        class Client:
            def __init__(self):
                self.calls = []

            async def iter_messages(self, entity, **kwargs):
                self.calls.append(kwargs)
                if "search" not in kwargs:
                    yield hit

        client = Client()
        matches = await _find_checkin_matches(client, object(), "lemony88")

        self.assertEqual(matches, [hit])
        self.assertEqual(client.calls, [
            {"search": "lemony88", "limit": 20},
            {"limit": 100},
        ])

    async def test_history_fallback_is_skipped_after_search_hit(self):
        hit = FakeMessage("签到资料 @lemony88", sender_id=1)

        class Client:
            def __init__(self):
                self.calls = []

            async def iter_messages(self, entity, **kwargs):
                self.calls.append(kwargs)
                if "search" in kwargs:
                    yield hit

        client = Client()
        matches = await _find_checkin_matches(client, object(), "lemony88")

        self.assertEqual(matches, [hit])
        self.assertEqual(client.calls, [{"search": "lemony88", "limit": 20}])


if __name__ == "__main__":
    unittest.main()
