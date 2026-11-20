"""Forward lineage extraction: channel / user / chat / hidden-author
forwards and non-forward messages."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from runner.forward_info import extract_forward_info


class ForwardExtractionTests(unittest.TestCase):
    def test_non_forward_message_returns_none(self):
        self.assertIsNone(extract_forward_info(SimpleNamespace(raw_text="普通消息")))
        self.assertIsNone(extract_forward_info(SimpleNamespace(fwd_from=None)))
        self.assertIsNone(extract_forward_info({}))  # 属性缺失也绝不抛错

    def test_channel_forward_extracts_origin_and_post(self):
        msg = SimpleNamespace(
            fwd_from=SimpleNamespace(
                from_id=SimpleNamespace(channel_id=424242),
                from_name=None,
                post_author="@mother_admin",
                channel_post=318,
                date=datetime(2026, 11, 19, 8, 30, tzinfo=timezone.utc),
            ),
        )
        info = extract_forward_info(msg)
        self.assertEqual(info["from_id"], 424242)
        self.assertEqual(info["from_type"], "channel")
        self.assertEqual(info["channel_post"], 318)
        self.assertEqual(info["post_author"], "@mother_admin")
        self.assertEqual(info["date"], "2026-11-19T08:30:00+00:00")

    def test_user_and_chat_forwards_resolve_their_ids(self):
        user_forward = SimpleNamespace(fwd_from=SimpleNamespace(
            from_id=SimpleNamespace(user_id=9001),
            date=None, from_name=None, post_author=None, channel_post=None,
        ))
        info = extract_forward_info(user_forward)
        self.assertEqual((info["from_id"], info["from_type"]), (9001, "user"))

        chat_forward = SimpleNamespace(fwd_from=SimpleNamespace(
            from_id=SimpleNamespace(chat_id=-777),
            date=None, from_name=None, post_author=None, channel_post=None,
        ))
        info = extract_forward_info(chat_forward)
        self.assertEqual((info["from_id"], info["from_type"]), (-777, "chat"))

    def test_hidden_author_forward_keeps_from_name(self):
        msg = SimpleNamespace(fwd_from=SimpleNamespace(
            from_id=None,  # 隐藏了来源 peer，只剩展示名
            from_name="某资料母频",
            post_author=None, channel_post=None, date=None,
        ))
        info = extract_forward_info(msg)
        self.assertIsNone(info["from_id"])
        self.assertEqual(info["from_name"], "某资料母频")
        self.assertIsNotNone(info)  # 仍有溯源价值，不能丢弃

    def test_degenerate_header_degrades_to_none(self):
        # 毫无溯源信号的空头（理论边界）：返回 None 而不是空 dict
        msg = SimpleNamespace(fwd_from=SimpleNamespace(
            from_id=None, from_name=None, post_author=None,
            channel_post=None, date=None,
        ))
        self.assertIsNone(extract_forward_info(msg))

    def test_broken_date_object_is_tolerated(self):
        msg = SimpleNamespace(fwd_from=SimpleNamespace(
            from_id=SimpleNamespace(channel_id=1),
            date=object(),  # isoformat 会炸的假日期
            from_name=None, post_author=None, channel_post=None,
        ))
        info = extract_forward_info(msg)
        self.assertEqual(info["from_id"], 1)
        self.assertTrue(info["date"])  # 兜底转成 str

    def test_album_style_first_forward_wins(self):
        """相册场景：首条带 fwd_from 的部件作为整组的溯源（与
        protocol._make_album_handler 的选择逻辑一致）。"""
        parts = [
            SimpleNamespace(fwd_from=None),
            SimpleNamespace(fwd_from=SimpleNamespace(
                from_id=SimpleNamespace(channel_id=424242),
                from_name="母频A", post_author=None,
                channel_post=11, date=datetime(2026, 11, 20, tzinfo=timezone.utc),
            )),
        ]
        first = next(
            (info for info in (extract_forward_info(p) for p in parts) if info),
            None,
        )
        self.assertEqual(first["from_name"], "母频A")


if __name__ == "__main__":
    unittest.main()
