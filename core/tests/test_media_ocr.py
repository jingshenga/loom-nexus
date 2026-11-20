import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from app.api.listings import _visible_media
from app.extractor.media_ocr import (
    MediaOcrProcessor, calc_file_sha256,
    classify_image, classify_profile_image, extract_card_fields, normalize_ocr_text,
)


class CalcFileSha256Tests(unittest.TestCase):
    def test_known_content_hashes_deterministically(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
            handle.write(b"nexus-media-bytes")
            path = handle.name
        try:
            with open(path, "rb") as raw:
                expected = hashlib.sha256(raw.read()).hexdigest()
            self.assertEqual(calc_file_sha256(__import__("pathlib").Path(path)), expected)
            # streaming twice yields the same digest
            self.assertEqual(calc_file_sha256(__import__("pathlib").Path(path)), expected)
        finally:
            import os
            os.unlink(path)

    def test_different_bytes_hash_differently(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as first:
            first.write(b"alpha")
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as second:
            second.write(b"beta")
        try:
            self.assertNotEqual(
                calc_file_sha256(__import__("pathlib").Path(first.name)),
                calc_file_sha256(__import__("pathlib").Path(second.name)),
            )
        finally:
            import os
            os.unlink(first.name)
            os.unlink(second.name)


class MediaOcrTests(unittest.TestCase):
    def test_normalization_removes_decorative_emoji_but_keeps_markers(self):
        value = normalize_ocr_text("🌹 服务：胸围 C+ @sample 1p500 禁无套")
        self.assertEqual(value, "服务:胸围 C+ @sample 1p500 禁无套")

    def test_dense_labelled_text_is_classified_as_private_card(self):
        lines = [
            {"text": "甜思雨"},
            {"text": "身高：165"},
            {"text": "体重：45"},
            {"text": "胸围：C"},
            {"text": "课费：1p500 2p900"},
            {"text": "服务：陪浴 丝袜 高跟鞋"},
            {"text": "禁无套 禁装机"},
        ]
        classification, confidence = classify_image(lines, 0.0, 0.72)
        self.assertEqual(classification, "info_card")
        self.assertGreaterEqual(confidence, 0.8)

    def test_short_caption_on_a_face_photo_remains_public(self):
        classification, _ = classify_image(
            [{"text": "周末快乐"}], 0.64, 0.35,
        )
        self.assertEqual(classification, "profile_photo")

    def test_profile_trial_uses_text_volume_and_line_count(self):
        classification, confidence, text = classify_profile_image(
            [{"text": "昵称：小雨"}, {"text": "身高：165"}, {"text": "价格：800"}], 15, 3,
        )
        self.assertEqual(classification, "info_card")
        self.assertGreater(confidence, 0.8)
        self.assertIn("身高", text)

    def test_profile_trial_marks_ambiguous_text_for_review(self):
        classification, confidence, _ = classify_profile_image(
            [{"text": "价格：800元，可联系看详情"}, {"text": "欢迎联系本人了解更多介绍内容"}], 40, 4,
        )
        self.assertEqual(classification, "uncertain")
        self.assertLess(confidence, 0.8)

    def test_explicit_age_wins_over_a_separate_birth_year(self):
        lines = [
            {"text": "年龄：19"},
            {"text": "身高：165cm"},
            {"text": "体重：90斤"},
            {"text": "胸围：B+"},
            {"text": "02年"},
            {"text": "课费：P800 45分钟"},
        ]
        fields, evidence = extract_card_fields(lines)
        self.assertEqual(fields["age_years"], 19)
        self.assertEqual(fields["height_cm"], 165)
        self.assertEqual(fields["weight_kg"], 45)
        self.assertEqual(fields["bust_size"], "B+")
        self.assertIn("02年", fields["unparsed_year_text"][0])
        self.assertIn("age_years", evidence)

    def test_card_normalizes_profile_and_keeps_original_line_evidence(self):
        lines = [
            {"text": "07年 / 168cm", "score": 0.97},
            {"text": "体重：165kg", "score": 0.8},
            {"text": "体重：１０５公斤", "score": 0.92},
            {"text": "🐻天然36C", "score": 0.91},
            {"text": "一次6章40分钟 / 双次12米80分钟", "score": 0.94},
        ]
        fields, evidence = extract_card_fields(lines)
        for key, expected, index in (
            ("age_years", 19, 0), ("weight_kg", 52.5, 2), ("bust_size", "C", 3),
        ):
            with self.subTest(field=key):
                self.assertEqual(fields[key], expected)
                self.assertEqual(evidence[key], {"line": lines[index]["text"], "score": lines[index]["score"]})
        self.assertEqual(fields["weight_value"], 105)
        self.assertEqual(fields["price_1p_text"], [lines[4]["text"]])
        self.assertEqual(fields["price_2p_text"], [lines[4]["text"]])
        self.assertNotIn("unparsed_year_text", fields)
        self.assertNotIn("stage_name_candidate", fields)

    def test_card_omits_invalid_body_and_duration_only_quotes(self):
        fields, _ = extract_card_fields([
            {"text": "出生：2009年"}, {"text": "体重79.9kg"},
            {"text": "胸围J"}, {"text": "双次40分钟"},
        ])
        self.assertTrue({"age_years", "weight_kg", "weight_value", "bust_size", "price_2p_text"}.isdisjoint(fields))

    def test_fields_capture_explicit_features_prices_and_fixed_intro_only(self):
        lines = [
            {"text": "价格：1P800 2P1500"},
            {"text": "服务：陪浴"},
            {"text": "特点：长发"},
            {"text": "禁嗑药 禁无套 禁醉酒 禁桩基 禁拔套 禁鸽子 禁变态"},
            {"text": "请携带现金"},
        ]
        fields, _ = extract_card_fields(lines)
        self.assertEqual(fields["features_text"], ["特点：长发"])
        self.assertEqual(fields["price_1p_text"], ["价格：1P800 2P1500"])
        self.assertEqual(fields["price_2p_text"], ["价格：1P800 2P1500"])
        self.assertEqual(fields["fixed_intro_terms"], [
            "禁嗑药", "禁无套", "禁醉酒", "禁桩基", "禁拔套", "禁鸽子", "禁变态",
        ])
        self.assertEqual(fields["cash_text"], ["请携带现金"])
        self.assertNotIn("stage_name_candidate", fields)

    def test_non_admin_media_does_not_include_unclassified_photos(self):
        media = [
            {"kind": "photo", "path": "card.jpg", "visibility": "admin"},
            {"kind": "photo", "path": "pending.jpg", "visibility": "pending"},
            {"kind": "photo", "path": "portrait.jpg", "visibility": "public"},
            {"kind": "video", "path": "clip.mp4"},
        ]
        public = _visible_media(media, False)
        self.assertEqual([item["path"] for item in public], ["portrait.jpg", "clip.mp4"])
        self.assertEqual(
            [item["path"] for item in _visible_media(media, True)],
            ["portrait.jpg", "pending.jpg", "card.jpg", "clip.mp4"],
        )


class FakeOcr:
    """Counts engine passes; returns a two-line info card."""

    def __init__(self):
        self.calls = 0

    async def read_document(self, path):
        self.calls += 1
        return {
            "ok": True,
            "lines": [
                {"text": "身高：165", "box": [[0, 0], [10, 0], [10, 5], [0, 5]]},
                {"text": "课费 8张", "box": [[0, 6], [10, 6], [10, 11], [0, 11]]},
            ],
            "texts": ["身高：165", "课费 8张"],
        }


class FakePool:
    """Dispatches on SQL text; the cache lookup returns the prepared row
    only after `arm_cache` (simulating: first pass = miss, second = hit)."""

    def __init__(self):
        self.cache_row = None
        self.finish_calls = []

    def arm_cache(self, finish_args):
        (_row_id, _status, classification, review_status,
         raw_text, normalized, fields, evidence, confidence,
         _engine, _error, _pred_class, _pred_conf, _version_id,
         _signals, _sha) = finish_args
        self.cache_row = {
            "classification": classification,
            "review_status": review_status,
            "raw_text": raw_text,
            "normalized_text": normalized,
            "fields_json": fields,
            "evidence_json": evidence,
            "confidence": confidence,
            "classifier_version_id": None,
            "classifier_signals_json": "{}",
        }

    async def fetchrow(self, sql, *args):
        if "file_sha256 = $1" in sql:
            row = self.cache_row
            return dict(row) if row else None
        if "raw_messages WHERE id" in sql:
            return {"media_json": "[]"}
        if "ocr_classifier_versions" in sql:
            return None
        return None

    async def fetch(self, sql, *args):
        if "SET status = $2" in sql:  # media_ocr_finish.sql
            self.finish_calls.append(args)
            return None
        if "media_ocr_set_flags" in sql or "dynamic_slang_terms" in sql:
            return None
        return []  # slang registry refresh


class MediaOcrCacheFlowTests(unittest.IsolatedAsyncioTestCase):
    def _processor_and_row(self):
        tmp = tempfile.mkdtemp()
        image = __import__("PIL.Image", fromlist=["Image"]).new("RGB", (4, 4), (10, 20, 30))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        (tmp and __import__("pathlib").Path(tmp, "card.jpg")).write_bytes(buffer.getvalue())
        pool = FakePool()
        ocr = FakeOcr()
        processor = MediaOcrProcessor(pool, tmp, ocr)
        row = {"id": 1, "media_path": "card.jpg", "profile_result_id": None,
               "raw_id": 7, "listing_id": None}
        return processor, pool, ocr, row

    async def test_first_pass_runs_engine_and_persists_sha256(self):
        processor, pool, ocr, row = self._processor_and_row()
        await processor._process(row)
        self.assertEqual(ocr.calls, 1)
        self.assertEqual(len(pool.finish_calls), 1)
        sha = pool.finish_calls[0][15]  # $16 file_sha256
        self.assertTrue(sha and len(sha) == 64)

    async def test_second_pass_hits_cache_and_skips_engine(self):
        processor, pool, ocr, row = self._processor_and_row()
        await processor._process(row)
        pool.arm_cache(pool.finish_calls[0])
        ocr.calls = 0
        await processor._process(row)
        self.assertEqual(ocr.calls, 0, "cache hit must skip the OCR engine")
        finish = pool.finish_calls[1]
        evidence = json.loads(finish[7])
        self.assertTrue(evidence.get("ocr_cache_hit") is True)
        self.assertEqual(evidence.get("cached_sha256"), finish[15])
        self.assertEqual(finish[2], pool.finish_calls[0][2])  # same classification

    async def test_failed_or_empty_records_are_never_cached(self):
        # DB-level guarantees baked into the lookup statement: only
        # successfully recognised, non-empty rows may serve the cache
        from app.extractor import media_ocr as module

        self.assertIn("status = 'done'", module.SQL_CACHE_LOOKUP)
        self.assertIn("raw_text <> ''", module.SQL_CACHE_LOOKUP)


if __name__ == "__main__":
    unittest.main()
