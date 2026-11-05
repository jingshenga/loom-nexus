import unittest

from app.extractor.ast_rules import extract_fields


class ExtractionRulesTest(unittest.TestCase):
    def test_removes_leading_hash_from_nickname_and_district(self):
        rule = {
            "extract": {
                "nickname": [r"艺名[:：]\s*([^\s\n]+)"],
                "district": [r"位置[:：]\s*([^\s\n]+)"],
            },
        }

        fields, _ = extract_fields(rule, "艺名：#小布丁\n位置：#鄞州")

        self.assertEqual(fields["nickname"], "小布丁")
        self.assertEqual(fields["district"], "鄞州")


if __name__ == "__main__":
    unittest.main()
