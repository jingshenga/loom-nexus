"""Local OCR triage for image-based listing cards."""
import logging
import re
import unicodedata
from pathlib import Path

from PIL import Image

from app import sqlres

logger = logging.getLogger("nexus-core.media-ocr")

SQL_DISCOVER = sqlres.load("media_ocr_discover.sql")
SQL_CLAIM = sqlres.load("media_ocr_claim.sql")
SQL_FINISH = sqlres.load("media_ocr_finish.sql")
SQL_MEDIA_FLAGS = sqlres.load("media_ocr_set_flags.sql")
SQL_RESET = sqlres.load("media_ocr_reset_processing.sql")

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
    def __init__(self, pool, media_dir: str, ocr_worker):
        self._pool = pool
        self._media_dir = Path(media_dir).resolve()
        self._ocr = ocr_worker

    async def run(self, stop) -> None:
        import asyncio

        await self._pool.fetch(SQL_RESET)
        logger.info("media-card OCR worker started")
        while not stop.is_set():
            try:
                await self._pool.fetch(SQL_DISCOVER)
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
        try:
            document = await self._ocr.read_document(str(media_path))
            if not document.get("ok"):
                raise RuntimeError(document.get("error") or "OCR worker failed")
            lines = document.get("lines", [])
            raw_text = "\n".join(str(item.get("text") or "").strip() for item in lines if item.get("text"))
            normalized = normalize_ocr_text(raw_text)
            if row["raw_id"] is not None:
                media = await self._pool.fetchrow(
                    "SELECT media_json FROM raw_messages WHERE id=$1", row["raw_id"],
                )
            else:
                media = await self._pool.fetchrow(
                    "SELECT media_json FROM listings WHERE id=$1", row["listing_id"],
                )
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
            classification, confidence = classify_image(lines, face_score, uniformity, dimensions)
            fields, evidence = extract_card_fields(lines)
            if classification in ("info_card", "uncertain"):
                review_status, visibility = "pending", "admin"
            else:
                review_status, visibility = "not_required", "public"
            await self._finish(
                row, "done", classification, review_status, visibility,
                raw_text, normalized, fields, evidence, confidence, None,
            )
            logger.info(
                "OCR image %s -> %s (chars=%d labels=%d confidence=%.2f)",
                row["id"], classification, len(normalized),
                sum(bool(p.search(raw_text)) for p in _LABELS), confidence,
            )
        except Exception as exc:
            logger.warning("OCR image %s failed: %s", row["id"], exc)
            await self._finish(row, "failed", "uncertain", "pending", "admin", "", "", {}, {}, 0.0, str(exc)[:500])

    async def _finish(
        self, row, status, classification, review_status, visibility,
        raw_text, normalized, fields, evidence, confidence, error,
    ):
        import json

        await self._pool.fetch(
            SQL_FINISH, row["id"], status, classification, review_status,
            raw_text, normalized, json.dumps(fields, ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False), confidence, ENGINE_VERSION, error,
        )
        await self._pool.fetch(SQL_MEDIA_FLAGS, row["raw_id"], row["media_path"], visibility, status, classification)
