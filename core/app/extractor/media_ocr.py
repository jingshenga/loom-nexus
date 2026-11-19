"""Local OCR triage for image-based listing cards."""
import asyncio
import logging
import json
import re
import unicodedata
from pathlib import Path

from PIL import Image

from app import sqlres
from app.extractor import ast_rules
from app.extractor.image_classifier import predict
from app.extractor.vision4_classifier import Vision4Classifier

logger = logging.getLogger("nexus-core.media-ocr")

SQL_DISCOVER = sqlres.load("media_ocr_discover.sql")
SQL_CLAIM = sqlres.load("media_ocr_claim.sql")
SQL_FINISH = sqlres.load("media_ocr_finish.sql")
SQL_MEDIA_FLAGS = sqlres.load("media_ocr_set_flags.sql")
SQL_RESET = sqlres.load("media_ocr_reset_processing.sql")
SQL_PROFILE_DISCOVER = sqlres.load("profile_collection_ocr_discover.sql")
SQL_PROFILE_FLAGS = sqlres.load("profile_collection_ocr_flags.sql")
SQL_ENQUEUE_RAW = sqlres.load("media_ocr_enqueue_raw.sql")
SQL_VISION4_INIT = sqlres.load("vision4_predictions_init.sql")
SQL_VISION4_UPSERT = sqlres.load("vision4_predictions_upsert.sql")
SQL_VISION4_ASSET_UPSERT = sqlres.load("vision4_asset_predictions_upsert.sql")
SQL_VISION4_PREDICTION_UPDATE = sqlres.load("vision4_prediction_fields_update.sql")
SQL_PROFILE_LISTING_SYNC = (
    "UPDATE listings SET nickname=$2,price_amount=$3,price_currency=$4,description=$5,"
    "custom_fields=$6::jsonb,evidence_json=$7::jsonb,"
    "media_json=CASE WHEN manual_fields ? 'media' THEN media_json ELSE $8::jsonb END,"
    "status=$9::varchar(32),published_at=CASE WHEN $9::varchar(32)='published' "
    "THEN COALESCE(published_at,NOW()) ELSE published_at END,"
    "updated_at=NOW() WHERE id=$1 AND status=$10 AND status IN ('pending','duplicate_pending') "
    "AND ($9::varchar(32)<>'published' OR NOT nexus_auto_duplicate_grouped(id)) "
    "AND updated_at=$11 AND manual_fields=$12::jsonb"
)

ENGINE_VERSION = "rapidocr-local-v1"
_DECORATIVE = re.compile(
    "[\\U0001F000-\\U0001FAFF\\U00002600-\\U000027BF]"
    "|[\\u200b-\\u200f\\ufeff]"
)
_KEEP_TEXT = re.compile(r"[^\w\u3400-\u9fff@+./#:%-]+", re.UNICODE)
_LABELS = (
    re.compile(r"艺名|昵称|身高|净高|体重|胸围|年龄"),
    re.compile(r"课费|水费|价格|收费|费用|\bprice\b|\bfee\b", re.I),
    re.compile(r"服务|课表|项目|套餐"),
    re.compile(r"禁|不接|不做|不接受|无套"),
)
_FIXED_INTRO = {
    "禁嗑药": re.compile(r"禁[\s:：，,、]*嗑药"),
    "禁无套": re.compile(r"禁[\s:：，,、]*无套"),
    "禁醉酒": re.compile(r"禁[\s:：，,、]*醉酒"),
    "禁桩基": re.compile(r"禁[\s:：，,、]*桩基"),
    "禁拔套": re.compile(r"禁[\s:：，,、]*拔套"),
    "禁鸽子": re.compile(r"禁[\s:：，,、]*鸽子"),
    "禁变态": re.compile(r"禁[\s:：，,、]*变态"),
}


def normalize_ocr_text(text: str) -> str:
    """Remove decorative symbols while preserving meaningful markers."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _DECORATIVE.sub(" ", text)
    text = _KEEP_TEXT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _box_area(box) -> float:
    if not box:
        return 0.0
    try:
        points = [(float(p[0]), float(p[1])) for p in box]
        if len(points) < 3:
            return 0.0
        return abs(sum(
            points[i][0] * points[(i + 1) % len(points)][1]
            - points[(i + 1) % len(points)][0] * points[i][1]
            for i in range(len(points))
        )) / 2
    except (TypeError, ValueError, IndexError):
        return 0.0


def image_signals(path: str) -> tuple[float, tuple[int, int] | None]:
    """Return dominant-background coverage and original image dimensions."""
    try:
        with Image.open(path) as image:
            dimensions = image.size
            sample = image.convert("RGB").resize((48, 48)).quantize(colors=8)
            histogram = sample.histogram()
            return max(histogram, default=0) / float(48 * 48), dimensions
    except Exception:
        return 0.0, None


def classify_image(
    lines: list,
    face_score: float | None,
    uniformity: float,
    dimensions: tuple[int, int] | None = None,
) -> tuple[str, float]:
    """Return (classification, confidence); uncertain cards remain private."""
    text = " ".join(str(line.get("text") or "") for line in lines)
    compact = re.sub(r"\s+", "", text)
    chars = len(compact)
    label_hits = sum(bool(pattern.search(text)) for pattern in _LABELS)
    line_count = sum(bool(str(line.get("text") or "").strip()) for line in lines)
    width = height = 0
    box_area = sum(_box_area(line.get("box")) for line in lines)
    for line in lines:
        box = line.get("box")
        if box:
            try:
                width = max(width, max(float(p[0]) for p in box))
                height = max(height, max(float(p[1]) for p in box))
            except (TypeError, ValueError, IndexError):
                pass
    image_width, image_height = dimensions or (width, height)
    text_coverage = min(1.0, box_area / (image_width * image_height)) if image_width and image_height else 0.0
    face_present = face_score is not None and face_score > 0.05

    if chars >= 30 and line_count >= 3 and label_hits >= 2 and (uniformity >= 0.24 or text_coverage >= 0.035):
        return "info_card", 0.94 if not face_present else 0.84
    if chars >= 72 and line_count >= 5 and (uniformity >= 0.36 or text_coverage >= 0.08):
        return "info_card", 0.88 if not face_present else 0.78
    if (chars >= 24 and label_hits >= 1 and line_count >= 2) or chars >= 54:
        return "uncertain", 0.55
    if face_present:
        return "profile_photo", 0.9
    return "other", 0.82 if chars < 18 else 0.6


def classify_profile_image(lines: list, min_chars: int, min_lines: int) -> tuple[str, float, str]:
    """Classify V2 profile photos by recognized text volume, without background color."""
    text_lines = [str(line.get("text") or "").strip() for line in lines]
    text_lines = [line for line in text_lines if line]
    text = " ".join(text_lines)
    compact = re.sub(r"\s+", "", text)
    chars = len(compact)
    count = len(text_lines)
    if chars >= min_chars and count >= min_lines:
        return "info_card", 0.9, text
    if chars >= max(12, min_chars // 2) or count >= max(2, min_lines - 1):
        return "uncertain", 0.55, text
    return "profile_photo", 0.82, text


def extract_card_fields(lines: list) -> tuple[dict, dict]:
    """Extract conservative candidates and keep line-level evidence."""
    fields: dict = {}
    evidence: dict = {}
    text_lines = [str(line.get("text") or "").strip() for line in lines]

    def take(key, pattern, cast=None):
        for index, line in enumerate(text_lines):
            match = re.search(pattern, line, re.I)
            if not match:
                continue
            value = match.group(1).strip()
            try:
                fields[key] = cast(value) if cast else value
            except (TypeError, ValueError):
                fields[key] = value
            evidence[key] = {
                "line": line,
                "score": lines[index].get("score"),
            }
            return

    title_candidates = [
        ( _box_area(line.get("box")), index, text_lines[index])
        for index, line in enumerate(lines)
        if 2 <= len(re.sub(r"\s+", "", text_lines[index])) <= 12
        and re.search(r"[\u3400-\u9fff]", text_lines[index])
        and not re.search(r"艺名|昵称|身高|体重|胸围|年龄|服务|特点|特征|特性|价格|课费|水费|现金|携带|禁", text_lines[index])
    ]
    if title_candidates and not any(re.search(r"(?:艺名|昵称)\s*[:：]?", line) for line in text_lines):
        _, index, title = max(title_candidates)
        fields["stage_name_candidate"] = title
        evidence["stage_name_candidate"] = {
            "line": title,
            "score": lines[index].get("score"),
        }

    take("stage_name", r"(?:艺名|昵称)\s*[:：]?\s*([^，,;；]+)")
    take("age_years", r"年龄\s*[:：]?\s*(\d{1,2})\s*(?:岁)?", int)
    take("height_cm", r"(?:身高|净高)\s*[:：]?\s*(\d{2,3})\s*(?:cm|厘米)?", int)

    weight_pattern = re.compile(r"体重\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(kg|公斤|斤|磅)?", re.I)
    for index, line in enumerate(text_lines):
        match = weight_pattern.search(line)
        if match:
            value, unit = match.group(1), (match.group(2) or "").lower()
            fields["weight_raw"] = match.group(0).strip()
            fields["weight_value"] = float(value) if "." in value else int(value)
            if unit in ("kg", "公斤"):
                fields["weight_kg"] = float(value)
            elif unit == "斤":
                fields["weight_kg"] = float(value) / 2
            evidence["weight_raw"] = {"line": line, "score": lines[index].get("score")}
            break

    take("bust_size", r"胸(?:围)?\s*[:：]?\s*([A-I](?:\+)?)", str.upper)

    locations = []
    services = []
    features = []
    restrictions = []
    fees = []
    one_person_fees = []
    two_person_fees = []
    cash_lines = []
    handles = []
    other = []
    fixed_intro_terms = set()
    for index, line in enumerate(text_lines):
        if not line:
            continue
        if re.search(r"地区|城区|地点|工作地|地址", line):
            locations.append(line)
        if re.search(r"服务|课表|服务详情|服务内容|项目|套餐", line):
            services.append(line)
        if re.search(r"特点|特征", line):
            features.append(line)
        if re.search(r"禁|不接|不做|不接受|无套", line):
            restrictions.append(line)
        for term, pattern in _FIXED_INTRO.items():
            if pattern.search(line):
                fixed_intro_terms.add(term)
        if re.search(r"现金|現金", line):
            cash_lines.append(line)
        if re.search(r"(?:^|[^\w])1\s*p", line, re.I):
            one_person_fees.append(line)
        if re.search(r"(?:^|[^\w])2\s*p", line, re.I):
            two_person_fees.append(line)
        if re.search(r"课费|水费|价格|收费|费用|\bP{1,2}\s*\d|\d\s*P{1,2}\b|\d+\s*分钟", line, re.I):
            fees.append(line)
        handles.extend(re.findall(r"@[A-Za-z0-9_]{3,}", line))
        if not any(line in group for group in (locations, services, features, restrictions, fees, cash_lines)):
            other.append(line)
    if locations:
        fields["location_text"] = locations
    if services:
        fields["service_text"] = services
    if features:
        fields["features_text"] = features
    if restrictions:
        fields["restriction_text"] = restrictions
    if fixed_intro_terms:
        fields["fixed_intro_terms"] = [term for term in _FIXED_INTRO if term in fixed_intro_terms]
    if cash_lines:
        fields["cash_text"] = cash_lines
    if fees:
        fields["price_text"] = fees
    if one_person_fees:
        fields["price_1p_text"] = one_person_fees
    if two_person_fees:
        fields["price_2p_text"] = two_person_fees
    if handles:
        fields["contact_handles"] = sorted(set(handles))
    year_mentions = [line for line in text_lines if re.search(r"\d{2}年", line)]
    if year_mentions:
        fields["unparsed_year_text"] = year_mentions
    if other:
        fields["other_text"] = other
    for key, group in (("location_text", locations), ("service_text", services),
                       ("restriction_text", restrictions), ("price_text", fees)):
        if group:
            source_line = group[0]
            line_index = text_lines.index(source_line)
            evidence[key] = {"line": source_line, "score": lines[line_index].get("score")}
    return fields, evidence


class MediaOcrProcessor:
    def __init__(
        self, pool, media_dir: str, ocr_worker,
        vision4_mode: str = "off",
        vision4_model_dir: str = "/app/data/models/vision-model-v1",
    ):
        self._pool = pool
        self._media_dir = Path(media_dir).resolve()
        self._ocr = ocr_worker
        self._vision4 = Vision4Classifier(vision4_mode, vision4_model_dir)
        self._vision4_gate = asyncio.Semaphore(1)

    async def initialize_vision4(self) -> None:
        try:
            await self._pool.execute(SQL_VISION4_INIT)
        except Exception:
            logger.exception("vision4 result storage unavailable; OCR continues without V4 predictions")
        if self._vision4.enabled:
            try:
                await asyncio.to_thread(self._vision4._ensure_loaded)
            except Exception:
                logger.exception("vision4 model initialization failed; OCR continues without V4 predictions")

    async def read_raw_texts(self, raw_id: int, media_paths: list[str]) -> tuple[list[str], bool]:
        """Reuse the persisted OCR result, or claim and process the same queue row."""
        texts = []
        pending = False
        for media_path in dict.fromkeys(media_paths):
            await self._pool.execute(SQL_ENQUEUE_RAW, raw_id, media_path)
            row = await self._pool.fetchrow(
                "SELECT id,status,raw_text FROM listing_media_ocr "
                "WHERE raw_id=$1 AND media_path=$2",
                raw_id, media_path,
            )
            if row is None:
                continue
            if row["status"] != "done":
                claimed = await self._pool.fetchval(
                    "UPDATE listing_media_ocr SET status='processing', attempts=attempts+1, "
                    "updated_at=NOW() WHERE id=$1 AND status='pending' RETURNING id",
                    row["id"],
                )
                if claimed:
                    task = await self._pool.fetchrow(
                        "SELECT id,raw_id,listing_id,profile_result_id,media_path "
                        "FROM listing_media_ocr WHERE id=$1",
                        row["id"],
                    )
                    await self._process(task)
                    row = await self._pool.fetchrow(
                        "SELECT status,raw_text FROM listing_media_ocr WHERE id=$1",
                        row["id"],
                    )
            if row and row["status"] == "done" and row["raw_text"]:
                texts.append(row["raw_text"])
            elif row and row["status"] in {"pending", "processing"}:
                pending = True
        return texts, pending

    async def run(self, stop) -> None:
        import asyncio

        await self._pool.fetch(SQL_RESET)
        logger.info("media-card OCR worker started")
        while not stop.is_set():
            try:
                await self._pool.execute(SQL_DISCOVER)
                await self._pool.execute(SQL_PROFILE_DISCOVER)
                row = await self._claim_one()
                if row:
                    await self._process(row)
                    continue
            except Exception:
                logger.exception("media-card OCR cycle failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass

    async def _claim_one(self):
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(SQL_CLAIM)
                if row:
                    await conn.execute(
                        "UPDATE listing_media_ocr SET status='processing', attempts=attempts+1, updated_at=NOW() WHERE id=$1",
                        row["id"],
                    )
                return row

    async def _process(self, row) -> None:
        media_path = (self._media_dir / row["media_path"]).resolve()
        if not media_path.is_relative_to(self._media_dir) or not media_path.is_file():
            await self._finish(row, "failed", "uncertain", "pending", "admin", "", "", {}, {}, 0.0, "image file missing")
            return
        media = await self._load_media(row)
        items = media["media_json"] if media else []
        if isinstance(items, str):
            import json
            items = json.loads(items)
        entry = next((
            item for item in items
            if item.get("path") == row["media_path"]
            or item.get("poster") == row["media_path"]
        ), {})
        face_score = entry.get("score")
        uniformity, dimensions = image_signals(str(media_path))
        signals = self._signals([], face_score, uniformity, dimensions)
        if await self._classifier_engine() == "vision4":
            await self._process_vision4(row, media_path, signals, face_score, uniformity, dimensions)
            return
        model_row = await self._load_active_classifier()
        try:
            document = await self._ocr.read_document(str(media_path))
            if not document.get("ok"):
                raise RuntimeError(document.get("error") or "OCR worker failed")
            lines = document.get("lines", [])
            signals = self._signals(lines, face_score, uniformity, dimensions)
            raw_text = "\n".join(str(item.get("text") or "").strip() for item in lines if item.get("text"))
            normalized = normalize_ocr_text(raw_text)
            if row["profile_result_id"] is not None:
                config = media["profile_config"] or {}
                if isinstance(config, str):
                    import json
                    config = json.loads(config)
                profile_text = " ".join(str(item.get("text") or "").strip() for item in lines if item.get("text"))
                if model_row:
                    classification, confidence, probabilities = predict(
                        model_row["model"], profile_text, signals,
                    )
                    version_id = model_row["id"]
                    review_status, visibility = self._route(model_row["policy"], classification, confidence)
                else:
                    classification, confidence, profile_text = classify_profile_image(
                        lines,
                        max(1, int(config.get("intro_min_chars", 30))),
                        max(1, int(config.get("intro_min_lines", 3))),
                    )
                    version_id = None
                    probabilities = {}
                    review_status = "pending" if classification in ("info_card", "uncertain") else "not_required"
                    visibility = "admin" if review_status == "pending" else "public"
                profile_fields, profile_evidence = extract_card_fields(lines)
                profile_evidence["class_probabilities"] = probabilities
                await self._finish(
                    row, "done", classification, review_status, visibility,
                    profile_text, normalize_ocr_text(profile_text), profile_fields,
                    profile_evidence, confidence, None,
                    version_id, signals,
                )
                return
            if model_row:
                classification, confidence, probabilities = predict(model_row["model"], raw_text, signals)
                version_id = model_row["id"]
                review_status, visibility = self._route(model_row["policy"], classification, confidence)
            else:
                classification, confidence = classify_image(lines, face_score, uniformity, dimensions)
                probabilities = {}
                version_id = None
                if classification in ("info_card", "uncertain"):
                    review_status, visibility = "pending", "admin"
                else:
                    review_status, visibility = "not_required", "public"
            fields, evidence = extract_card_fields(lines)
            evidence["classifier"] = {"probabilities": probabilities} if probabilities else {}
            await self._finish(
                row, "done", classification, review_status, visibility,
                raw_text, normalized, fields, evidence, confidence, None,
                version_id, signals,
            )
            logger.info(
                "OCR image %s -> %s (chars=%d labels=%d confidence=%.2f)",
                row["id"], classification, len(normalized),
                sum(bool(p.search(raw_text)) for p in _LABELS), confidence,
            )
        except Exception as exc:
            logger.warning("OCR image %s failed: %s", row["id"], exc)
            classification, confidence, version_id = "uncertain", 0.0, None
            evidence = {}
            if model_row:
                classification, confidence, probabilities = predict(model_row["model"], "", signals)
                version_id = model_row["id"]
                evidence = {"classifier": {"probabilities": probabilities}}
            await self._finish(
                row, "failed", classification, "pending", "admin", "", "", {},
                evidence, confidence, str(exc)[:500], version_id, signals,
            )

    async def _classifier_engine(self) -> str:
        value = await self._pool.fetchval(
            "SELECT value FROM app_settings WHERE key='ocr_classifier_engine'"
        )
        return value if value in ("vision4", "legacy") else (
            "vision4" if self._vision4.mode == "active" else "legacy"
        )

    async def _process_vision4(self, row, media_path: Path, signals, face_score, uniformity, dimensions) -> None:
        try:
            async with self._vision4_gate:
                result = await asyncio.to_thread(self._vision4.predict, str(media_path))
        except Exception:
            logger.exception("vision4 inference task failed (ocr_id=%s)", row["id"])
            result = {"status": "error", "error_code": "inference_error", "scores": {}}
        classification = result.get("mapped_classification") if result.get("status") == "classified" else None
        classification = classification or "uncertain"
        scores = result.get("scores") or {}
        confidence = max(scores.values()) if scores else 0.0
        try:
            document = await self._ocr.read_document(str(media_path))
            if not document.get("ok"):
                raise RuntimeError(document.get("error") or "OCR worker failed")
            lines = document.get("lines", [])
            signals = self._signals(lines, face_score, uniformity, dimensions)
            raw = "\n".join(str(item.get("text") or "").strip() for item in lines if item.get("text"))
            if row["profile_result_id"] is not None:
                raw = " ".join(str(item.get("text") or "").strip() for item in lines if item.get("text"))
            fields, evidence = extract_card_fields(lines)
            evidence["classifier"] = {"engine": "vision4", "scores": scores}
            await self._finish(row, "done", classification, "pending", "admin", raw,
                               normalize_ocr_text(raw), fields, evidence, confidence, None,
                               signals=signals)
        except Exception as exc:
            logger.warning("OCR image %s failed: %s", row["id"], exc)
            await self._finish(row, "failed", classification, "pending", "admin", "", "", {},
                               {"classifier": {"engine": "vision4", "scores": scores}},
                               confidence, str(exc)[:500], signals=signals)
        await self._run_vision4(row, media_path, result)

    async def _run_vision4(self, row, media_path: Path, result=None) -> bool:
        if not self._vision4.enabled:
            return False
        if result is None:
            try:
                async with self._vision4_gate:
                    result = await asyncio.to_thread(self._vision4.predict, str(media_path))
            except Exception:
                logger.warning("vision4 inference task failed (ocr_id=%s); continuing OCR", row["id"], exc_info=True)
                return False
        version = result.get("model_version") or self._vision4.model_dir.name
        model_sha = result.get("model_sha")
        model_key = f"{version}:{model_sha or result.get('error_code', 'unavailable')}"
        status = result.get("status", "unavailable")
        category = result.get("category")
        mapped = result.get("mapped_classification")
        category_zh = result.get("category_zh")
        scores = result.get("scores") or {}
        score = max(scores.values()) if scores else None
        try:
            await self._pool.execute(
                SQL_VISION4_UPSERT,
                row["id"], model_key, version, model_sha, self._vision4.mode,
                status, category, mapped, category_zh, json.dumps(scores),
                result.get("score_semantics"), result.get("model_load_ms"),
                result.get("inference_ms"), result.get("error_code"),
            )
        except Exception:
            logger.warning("vision4 result persistence failed (ocr_id=%s)", row["id"], exc_info=True)
        try:
            await self._pool.execute(
                SQL_VISION4_ASSET_UPSERT,
                row["media_path"], model_key, row["id"], version, model_sha,
                self._vision4.mode, status, category, mapped, category_zh,
                json.dumps(scores), result.get("score_semantics"),
                result.get("model_load_ms"), result.get("inference_ms"),
                result.get("error_code"),
            )
        except Exception:
            logger.warning("vision4 asset persistence failed (ocr_id=%s)", row["id"], exc_info=True)
        if self._vision4.mode == "active" and status == "classified" and mapped:
            try:
                await self._pool.execute(
                    SQL_VISION4_PREDICTION_UPDATE,
                    row["media_path"], mapped, score, version, model_sha,
                    result.get("score_semantics"),
                )
                return True
            except Exception:
                logger.warning("vision4 prediction-field update failed (ocr_id=%s)", row["id"], exc_info=True)
        return False

    async def _load_media(self, row):
        if row["profile_result_id"] is not None:
            return await self._pool.fetchrow(
                "SELECT media_json,profile_config FROM profile_collection_results WHERE id=$1",
                row["profile_result_id"],
            )
        if row["raw_id"] is not None:
            return await self._pool.fetchrow(
                "SELECT media_json FROM raw_messages WHERE id=$1", row["raw_id"],
            )
        return await self._pool.fetchrow(
            "SELECT media_json FROM listings WHERE id=$1", row["listing_id"],
        )

    async def _load_active_classifier(self):
        row = await self._pool.fetchrow(
            "SELECT id,model_json,policy_json FROM ocr_classifier_versions "
            "WHERE status='active' ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        import json
        model = row["model_json"] if isinstance(row["model_json"], dict) else json.loads(row["model_json"])
        policy = row["policy_json"] if isinstance(row["policy_json"], dict) else json.loads(row["policy_json"])
        return {"id": row["id"], "model": model, "policy": policy}

    @staticmethod
    def _signals(lines, face_score, uniformity, dimensions):
        texts = [str(item.get("text") or "").strip() for item in lines if item.get("text")]
        chars = len(re.sub(r"\s+", "", "".join(texts)))
        box_area = sum(_box_area(item.get("box")) for item in lines)
        width, height = dimensions or (0, 0)
        coverage = min(1.0, box_area / (width * height)) if width and height else 0.0
        return {
            "face_score": face_score,
            "uniformity": round(float(uniformity), 5),
            "text_chars": chars,
            "text_lines": len(texts),
            "text_coverage": round(coverage, 5),
        }

    @staticmethod
    def _route(policy, classification, confidence):
        review_classes = set(policy.get("review_classes") or ["info_card", "uncertain"])
        pending = classification in review_classes or confidence < float(policy.get("confidence_threshold", 0.65))
        return ("pending", "admin") if pending else ("not_required", "public")

    async def _finish(
        self, row, status, classification, review_status, visibility,
        raw_text, normalized, fields, evidence, confidence, error,
        version_id=None, signals=None,
    ):
        import json

        finished = await self._pool.fetchrow(
            SQL_FINISH, row["id"], status, classification, review_status,
            raw_text, normalized, json.dumps(fields, ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False), confidence, ENGINE_VERSION, error,
            classification, confidence, version_id,
            json.dumps(signals or {}, ensure_ascii=False),
        )
        if finished is None:
            return
        classification = finished["classification"]
        review_status = finished["review_status"]
        if review_status == "confirmed":
            visibility = "admin"
        elif review_status == "not_card":
            visibility = "public"
        if row["profile_result_id"] is not None:
            await self._pool.fetch(
                SQL_PROFILE_FLAGS, row["profile_result_id"], row["media_path"],
                status, classification, review_status, raw_text, confidence, visibility,
            )
            await self._sync_profile_listing(row["profile_result_id"])
        else:
            await self._pool.fetch(SQL_MEDIA_FLAGS, row["raw_id"], row["media_path"], visibility, status, classification)

    async def _sync_profile_listing(self, profile_result_id: int) -> None:
        result = await self._pool.fetchrow(
            "SELECT r.description AS source_description,r.evidence_json,r.media_json,"
            "j.source_id,l.id AS listing_id,l.nickname,l.price_amount,l.price_currency,"
            "l.description,l.contact_username,l.bot_url,l.custom_fields,l.manual_fields,"
            "l.evidence_json AS listing_evidence,l.status,l.updated_at AS listing_updated_at,"
            "j.collection_plan_json "
            "FROM profile_collection_results r "
            "JOIN profile_collection_jobs j ON j.id=r.job_id "
            "JOIN listings l ON l.profile_collection_job_id=j.id "
            "WHERE r.id=$1",
            profile_result_id,
        )
        if result is None or result["status"] not in {"pending", "duplicate_pending"}:
            return
        ocr_rows = await self._pool.fetch(
            "SELECT status,COALESCE(human_classification,classification) AS classification,"
            "raw_text,fields_json FROM listing_media_ocr WHERE profile_result_id=$1",
            profile_result_id,
        )
        if any(row["status"] in {"pending", "processing"} for row in ocr_rows):
            return

        source_text = result["source_description"] or ""
        ocr_text = "\n".join(row["raw_text"] for row in ocr_rows if row["raw_text"])
        intro_text = "\n".join(
            row["raw_text"] for row in ocr_rows
            if row["raw_text"] and row["classification"] == "info_card"
        )
        intro_marker = "图片识别内容：" + intro_text.strip()
        description = source_text.strip()
        if intro_text and intro_marker not in description:
            description = "\n\n".join(part for part in (description, intro_marker) if part)

        listing_rule = await self._pool.fetchrow(
            "SELECT r.config_json FROM source_rule_bindings b "
            "JOIN ingest_rules r ON r.id=b.rule_id AND r.rule_type='listing' AND r.is_active "
            "WHERE b.source_id=$1 ORDER BY b.priority DESC,r.id LIMIT 1",
            result["source_id"],
        )
        rule = listing_rule["config_json"] if listing_rule else {}
        if isinstance(rule, str):
            rule = json.loads(rule)
        text_fields, _ = ast_rules.extract_fields(rule, source_text, src="telegram")
        image_fields, _ = ast_rules.extract_fields(rule, ocr_text, src="ocr")
        image_field_rows = [
            row["fields_json"] if isinstance(row["fields_json"], dict)
            else json.loads(row["fields_json"] or "{}") for row in ocr_rows
        ]
        if not image_fields:
            stage_name = next((item.get("stage_name") for item in image_field_rows if item.get("stage_name")), None)
            if stage_name:
                image_fields["nickname"] = stage_name

        field_definitions = await self._pool.fetch(
            "SELECT field_key,aliases,extraction_source,required_for_publish "
            "FROM listing_field_definitions WHERE is_active"
        )
        from app.extractor.pipeline import extract_custom_fields

        custom_fields = _decode_json(result["custom_fields"], {})
        telegram_custom = extract_custom_fields(
            source_text, [row for row in field_definitions if row["extraction_source"] == "telegram"],
        )
        ocr_custom = extract_custom_fields(
            ocr_text, [row for row in field_definitions if row["extraction_source"] == "ocr"],
        )
        custom_updates = {}
        manual = _decode_json(result["manual_fields"], {})
        if "custom_fields" not in manual:
            for values in (telegram_custom, ocr_custom):
                for key, value in values.items():
                    if not str(custom_fields.get(key) or "").strip():
                        custom_fields[key] = value
                        custom_updates[key] = value

        nickname = result["nickname"]
        if "nickname" not in manual and not str(nickname or "").strip():
            nickname = text_fields.get("nickname") or image_fields.get("nickname")
        price_amount = result["price_amount"]
        if "price_amount" not in manual and price_amount is None:
            price_amount = text_fields.get("price_amount") or image_fields.get("price_amount")
        price_currency = result["price_currency"] or "CNY"
        if "price_currency" not in manual and result["price_amount"] is None:
            price_currency = text_fields.get("price_currency") or image_fields.get("price_currency") or price_currency
        if "description" in manual:
            description = result["description"] or ""

        ocr_filled = bool(
            (not result["nickname"] and image_fields.get("nickname"))
            or (result["price_amount"] is None and image_fields.get("price_amount") is not None)
            or ("description" not in manual and intro_text)
            or any(
                definition["required_for_publish"]
                and definition["field_key"] in custom_updates
                for definition in field_definitions
            )
        )
        listing_evidence = _decode_json(result["listing_evidence"], {})
        if ocr_filled:
            listing_evidence["ocr_filled_fields"] = {
                "src": "ocr", "fields": image_fields,
                "custom_fields": {key: value for key, value in ocr_custom.items() if key in custom_updates},
            }
        next_status = result["status"]
        if next_status == "pending" and not ocr_filled and not _decode_json(result["collection_plan_json"], {}):
            ready = await self._pool.fetchval(
                "SELECT listing_meets_publish_requirements($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)",
                nickname or "", price_amount, description, result["contact_username"],
                result["bot_url"] or "", result["media_json"],
                json.dumps(custom_fields, ensure_ascii=False),
            )
            if ready:
                next_status = "published"
        await self._pool.execute(
            SQL_PROFILE_LISTING_SYNC,
            result["listing_id"], nickname, price_amount, price_currency, description,
            json.dumps(custom_fields, ensure_ascii=False),
            json.dumps(listing_evidence, ensure_ascii=False), result["media_json"], next_status,
            result["status"], result["listing_updated_at"], json.dumps(manual, ensure_ascii=False),
        )


def _decode_json(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback
