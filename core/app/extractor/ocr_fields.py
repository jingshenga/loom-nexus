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

from app.extractor.ast_rules import clean_username

# body-data keys the pipeline treats as a core-field trigger group
BODY_FIELD_KEYS = ("height_cm", "weight_kg", "bust_size", "age_years")

_DISTRICT_LABEL = re.compile(
    r"^(?:工作地点|工作地|地区|城区|地点|地址|位置)[址置]?\s*[:：]?\s*"
)
# "1P800 2P1500" mode quotes; a digit before P excludes plain amounts
_PRICE_MODE = re.compile(r"(?<![0-9A-Za-z])([12])\s*[pP]\s*(\d{2,5})(?![\d])")
# standalone amounts, but not "45分钟" / "165cm" / the "800" inside "1P800"
_PLAIN_AMOUNT = re.compile(
    r"(?<![0-9A-Za-z.])(\d{2,5})(?![.\d])(?!\s*(?:分钟|分|min|cm|kg|岁))",
    re.I,
)


def amounts_from_lines(lines):
    """Fee lines → (mode_quotes, first_plain_amount); mode quotes are
    the 1p/2p ("1P800" → {"1p": 800.0}) splits, the plain amount is the
    fallback for unlabeled fee lines ("课费：800" → 800.0)."""
    modes: dict = {}
    plain = None
    for line in lines or []:
        line = str(line)
        for match in _PRICE_MODE.finditer(line):
            modes.setdefault(f"{match.group(1)}p", float(match.group(2)))
        if plain is None:
            match = _PLAIN_AMOUNT.search(line)
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

    # price: the 1-person quote is the canonical price, then 2p, then
    # the first plain amount on a fee/cash line
    modes, plain = amounts_from_lines(
        (card_fields.get("price_1p_text") or [])
        + (card_fields.get("price_2p_text") or [])
        + (card_fields.get("price_text") or [])
        + (card_fields.get("cash_text") or [])
    )
    amount = modes.get("1p") or modes.get("2p") or plain
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
