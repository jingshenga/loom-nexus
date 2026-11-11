import unittest

from app.extractor.lifecycle_identity import explicit_lifecycle_username


class LifecycleIdentityTests(unittest.TestCase):
    def test_requires_one_explicit_target_matching_extracted_tg(self):
        self.assertEqual(
            explicit_lifecycle_username("已售，联系 @target_user", "target_user"),
            "target_user",
        )
        self.assertEqual(
            explicit_lifecycle_username("账号：target_user 已休息", "target_user"),
            "target_user",
        )

    def test_sender_or_nickname_is_not_a_target(self):
        self.assertIsNone(explicit_lifecycle_username("已售，柒柒休息", "qiqi12345"))
        self.assertIsNone(explicit_lifecycle_username("已售", "sender_bot"))

    def test_multiple_or_ambiguous_tg_identities_are_rejected(self):
        self.assertIsNone(
            explicit_lifecycle_username("@first_user 已售，联系 @second_user", "first_user")
        )
        self.assertIsNone(explicit_lifecycle_username("@target_user 已售", "other_user"))


if __name__ == "__main__":
    unittest.main()
