import unittest
from types import SimpleNamespace

from runner.history_scan import _rule_candidate


class HistoryScanCandidateTests(unittest.TestCase):
    def test_requires_relevant_identity_for_media_only_message(self):
        class Message:
            raw_text = ""
            photo = object()
            video = None

            def get_entities_text(self):
                return [(SimpleNamespace(url="https://t.me/mmmeee321"), "@mmmeee321")]

        self.assertTrue(_rule_candidate(Message(), {}))
        Message.get_entities_text = lambda self: []
        self.assertFalse(_rule_candidate(Message(), {}))

    def test_skips_promotional_and_lifecycle_messages(self):
        class Message:
            raw_text = "TG：@mmmeee321 博彩招代理"
            sender_id = 1

            def get_entities_text(self):
                return []

        self.assertFalse(_rule_candidate(Message(), {}))
        Message.raw_text = "TG：@mmmeee321 已售"
        self.assertFalse(_rule_candidate(Message(), {"lifecycle_terms": ["已售"]}))

    def test_applies_listing_keyword_gate_but_keeps_exact_username_match(self):
        class Message:
            raw_text = "这是一段介绍"
            sender_id = 1

            def get_entities_text(self):
                return []

        self.assertFalse(_rule_candidate(Message(), {"gate": {"require_any_keywords": ["老师编号"]}}))
        Message.raw_text = "这是一段介绍 @mmmeee321"
        self.assertTrue(_rule_candidate(Message(), {"gate": {"require_any_keywords": ["老师编号"]}}))


if __name__ == "__main__":
    unittest.main()
