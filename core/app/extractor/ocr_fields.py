"""OCR structured fields → canonical listing fields (cross-fill).

The media OCR worker (media_ocr.py) already turns listing-card images
into structured per-image fields (stage_name, price_1p_text,
location_text, height_cm, ...). This module maps those card fields
into the same (fields, evidence) shape produced by
ast_rules.extract_fields, so the pipeline can merge them into a
listing whenever the message text alone misses a core field — with
provenance kept on every filled value: source line, per-line OCR
score and the card-level OCR confidence.

Pure functions only — no I/O.
"""
import re

from app.extractor import ast_rules
from app.extractor.ast_rules import clean_username

# body-data keys the pipeline treats as a core-field trigger group
BODY_FIELD_KEYS = ("height_cm", "weight_kg", "bust_size", "age_years")

_DISTRICT_LABEL = re.compile(
    r"^(?:工作地点|工作地|地区|城区|地点|地址|位置)[址置]?\s*[:：]?\s*"
)
# ---------------------------------------------------------------------------
# tiered tariffs (1P/2P): three mainstream syntaxes, parsed AFTER folding
# each line through ast_rules.normalize_input (keycap emoji 1️⃣→1,
# pointing marks → spaces, money slang "P 6米" → "P 600", full-width fold).
#   A suffix-style:  600p / 600/p / 600 元 p / 600次 / 900pp / 1200 2p
#   B prefix-style:  1P800 / 1P: 600 / 1P👉600 / P 6米 / 1次500 / 2次 1200
#   C slash-style:   600/p (covered by the suffix arm of A)
# ---------------------------------------------------------------------------
_P1_PREFIX_COMPACT = re.compile(
    r"(?<![0-9A-Za-z])1\s*[pP次]\s*[:：👉\s]*([0-9]{2,5})(?![\d])"
)
_P1_PREFIX_BARE = re.compile(
    r"(?<![0-9A-Za-z])[pP]\s*[:：👉\s]*([0-9]{2,5})(?![\d])"
)
_P1_SUFFIX = re.compile(
    r"(?<!\d)([0-9]{3,5})\s*(?:元|块)?\s*(?:/|\s)?\s*(?:[pP]|次)\b"
)
_P2_PREFIX_COMPACT = re.compile(
    r"(?<![0-9A-Za-z])2\s*[pP次]\s*[:：👉\s]*([0-9]{2,5})(?![\d])"
)
_P2_PREFIX_PP = re.compile(
    r"(?<![0-9A-Za-z])(?:pp|2\s*[pP])\s*[:：👉\s]*([0-9]{2,5})(?![\d])",
    re.I,
)
_P2_SUFFIX = re.compile(
    r"(?<!\d)([0-9]{3,5})\s*(?:元|块)?\s*(?:/|\s)?\s*(?:pp|2p)\b",
    re.I,
)
_P1_PATTERNS = (_P1_PREFIX_COMPACT, _P1_PREFIX_BARE, _P1_SUFFIX)
_P2_PATTERNS = (_P2_PREFIX_COMPACT, _P2_PREFIX_PP, _P2_SUFFIX)
# duration inside a fee line ("40分钟", "40min", "40 分")
_DURATION = re.compile(r"([0-9]{1,3})\s*(?:分钟|分|min)\b", re.I)
# standalone amounts, but not "45分钟" / "165cm" / the "800" inside "1P800"
_PLAIN_AMOUNT = re.compile(
    r"(?<![0-9A-Za-z.])(\d{2,5})(?![.\d])(?!\s*(?:分钟|分|min|cm|kg|岁))",
    re.I,
)


def _first_quote_hit(folded: str, patterns):
    """(amount, match_end) of the first matching quote pattern, or None.
    Lines are pre-folded so slang units (章/米/张) are already yuan."""
    for pattern in patterns:
        match = pattern.search(folded)
        if match:
            return float(match.group(1)), match.end()
    return None


def _duration_after(folded: str, pos: int):
    """First duration mentioned after the price position on the line."""
    match = _DURATION.search(folded, pos)
    return int(match.group(1)) if match else None


def format_quote(amount: float, minutes=None) -> str:
    text = f"{int(round(amount))} 元"
    if minutes:
        text += f" ({minutes}分钟)"
    return text


def format_fee_text(line):
    """Dirty fee line → clean display text ("600 元 (40分钟)"); None
    when the line carries no 1P/2P quote (it stays a raw fee)."""
    folded = ast_rules.money_normalize(str(line))
    for patterns in (_P1_PATTERNS, _P2_PATTERNS):
        hit = _first_quote_hit(folded, patterns)
        if hit:
            return format_quote(hit[0], _duration_after(folded, hit[1]))
    return None


def tariff_quotes(lines) -> dict:
    """Fee lines → {"1p": {"amount", "minutes", "text", "line"}, "2p": …}.
    First quote per tier wins; duration is taken from the same line,
    after the price."""
    quotes: dict = {}
    for line in lines or []:
        folded = ast_rules.normalize_input(str(line))
        for key, patterns in (("1p", _P1_PATTERNS), ("2p", _P2_PATTERNS)):
            if key in quotes:
                continue
            hit = _first_quote_hit(folded, patterns)
            if hit:
                minutes = _duration_after(folded, hit[1])
                quotes[key] = {
                    "amount": hit[0],
                    "minutes": minutes,
                    "text": format_quote(hit[0], minutes),
                    "line": str(line),
                }
    return quotes


def amounts_from_lines(lines):
    """Fee lines → (mode_quotes, first_plain_amount); mode quotes are
    the 1p/2p splits across all three syntaxes ({"1p": 600.0}), the
    plain amount is the fallback for unlabeled fee lines ("课费：800").
    Lines that already carry a 1P/2P quote are excluded from the plain
    scan so nothing is double-counted."""
    modes: dict = {}
    plain = None
    for line in lines or []:
        folded = ast_rules.normalize_input(str(line))
        has_mode = False
        for key, patterns in (("1p", _P1_PATTERNS), ("2p", _P2_PATTERNS)):
            if key in modes:
                continue
            hit = _first_quote_hit(folded, patterns)
            if hit:
                modes[key] = hit[0]
                has_mode = True
        if plain is None and not has_mode:
            match = _PLAIN_AMOUNT.search(folded)
            if match:
                plain = float(match.group(1))
    return modes, plain


def card_to_listing_fields(card_fields, card_evidence=None, ocr_confidence=None):
    """Map OCR card fields to canonical listing fields. Every evidence
    entry carries src="ocr" plus the card confidence and the OCR line
    (and line score when known) so admin review can trace each filled
    value back to the pixel it came from."""
    fields: dict = {}
    evidence: dict = {}
    card_evidence = card_evidence or {}

    def put(key, value, source_line=None, line_score=None):
        entry = {"value": value, "src": "ocr"}
        if ocr_confidence is not None:
            entry["ocr_confidence"] = ocr_confidence
        if source_line:
            entry["line"] = str(source_line)
        if line_score is not None:
            entry["ocr_line_score"] = line_score
        fields[key] = value
        evidence[key] = entry

    # nickname: the explicit label wins, else the conservative title
    # candidate the card extractor picked
    nickname = card_fields.get("stage_name") or card_fields.get("stage_name_candidate")
    if nickname:
        card_ev = (
            card_evidence.get("stage_name")
            or card_evidence.get("stage_name_candidate")
            or {}
        )
        put("nickname", str(nickname).strip(), card_ev.get("line"), card_ev.get("score"))

    handles = card_fields.get("contact_handles") or []
    if handles:
        username = clean_username(str(handles[0]))
        if username:
            put("username", username, handles[0], None)

    location = (card_fields.get("location_text") or [None])[0]
    if location:
        district = _DISTRICT_LABEL.sub("", str(location)).strip(" ，,;；、")
        if district:
            card_ev = card_evidence.get("location_text") or {}
            put("district", district, location, card_ev.get("score"))

    # price: tiered quotes across all three 1P/2P syntaxes, then the
    # first plain amount on an unlabeled fee/cash line. Structured
    # price_1p/price_2p carry the numeric value plus the clean display
    # text ("600 元 (40分钟)") so the frontend never shows dirty lines.
    fee_lines = (
        (card_fields.get("price_1p_text") or [])
        + (card_fields.get("price_2p_text") or [])
        + (card_fields.get("price_text") or [])
        + (card_fields.get("cash_text") or [])
    )
    quotes = tariff_quotes(fee_lines)
    modes, plain = amounts_from_lines(fee_lines)
    for key, display_key in (("1p", "price_1p"), ("2p", "price_2p")):
        quote = quotes.get(key)
        if quote:
            put(f"{display_key}", quote["amount"], quote["line"], None)
            put(f"{display_key}_display", quote["text"], quote["line"], None)
    amount = (
        (quotes.get("1p") or {}).get("amount")
        or (quotes.get("2p") or {}).get("amount")
        or plain
    )
    if amount is not None:
        price_line = None
        for group in ("price_1p_text", "price_2p_text", "price_text", "cash_text"):
            lines = card_fields.get(group) or []
            if lines:
                price_line = lines[0]
                break
        card_ev = card_evidence.get("price_text") or {}
        put("price_amount", amount, price_line, card_ev.get("score"))

    # body data: passed through as extra fields (no listing columns;
    # they land in evidence_json for the review workbench)
    for key in BODY_FIELD_KEYS:
        value = card_fields.get(key)
        if value not in (None, ""):
            card_ev = card_evidence.get(key) or {}
            put(key, value, card_ev.get("line"), card_ev.get("score"))

    return fields, evidence
