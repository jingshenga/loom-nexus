import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from runner.duty import business_window, linked_posts, message_matches, profile_usernames


class FakeMessage:
    def __init__(self, text, sender_id, urls=()):
        self.raw_text = text
        self.sender_id = sender_id
        self._urls = urls

    def get_entities_text(self):
        return [(SimpleNamespace(url=url), "link") for url in self._urls]


class DutyRuleTests(unittest.TestCase):
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

    def test_xiaomuwu_keeps_only_target_channel_posts(self):
        message = FakeMessage("在线老师", 1, [
            "https://t.me/NBXMWBGJ/5153",
            "https://t.me/another/99",
        ])
        posts = linked_posts(message, "NBXMWBGJ")
        self.assertEqual(posts[0][0:2], ("NBXMWBGJ", 5153))
        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    unittest.main()
