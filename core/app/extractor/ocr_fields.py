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
from math import isfinite

from app.extractor import ast_rules
from app.extractor.ast_rules import clean_username, tariff_quotes

# body-data keys the pipeline treats as a core-field trigger group
BODY_FIELD_KEYS = ("height_cm", "weight_kg", "bust_size", "age_years")


def card_weight_kg(fields) -> float | None:
    """Reinterpret legacy raw weights while keeping stored kg idempotent."""
    raw = fields.get("weight_raw")
    if raw:
        if weight := ast_rules.find_weight(str(raw)):
            return weight[2]
        return ast_rules.normalize_weight(raw)
    kg = fields.get("weight_kg")
    if kg in (None, ""):
        return ast_rules.normalize_weight(fields.get("weight_value"))
    try:
        kg = float(re.sub(r"\s*(?:kg|公斤)$", "", str(kg).strip(), flags=re.I))
    except (TypeError, ValueError):
        return None
    if not isfinite(kg) or kg < 19:
        return None
    return round(kg, 1) if kg <= 80 else ast_rules.normalize_weight(kg)


_DISTRICT_LABEL = re.compile(
    r"^(?:工作地点|工作地|地区|城区|地点|地址|位置)[址置]?\s*[:：]?\s*"
)
# Plain fees like "课费：800" are the fallback when no tier is present.
_PLAIN_AMOUNT = re.compile(
    r"(?<![0-9A-Za-z.])(\d{2,5})(?![.\d])(?!\s*(?:分钟|分|min|cm|kg|岁))",
    re.I,
)


def format_fee_text(line, tier=None):
    """Return a tier's clean display text, or the first quote if unspecified."""
    quotes = tariff_quotes([line])
    quote = quotes.get(tier) if tier else (quotes.get("1p") or quotes.get("2p") or quotes.get("bt"))
    return quote["text"] if quote else None


def amounts_from_lines(lines):
    """Return the first amount per tier and the first untiered fee."""
    modes = {}
    plain = None
    for line in lines or []:
        quotes = tariff_quotes([line])
        for tier, quote in quotes.items():
            modes.setdefault(tier, quote["amount"])
        if plain is None and not quotes:
            match = _PLAIN_AMOUNT.search(ast_rules.normalize_input(str(line)))
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
    _, plain = amounts_from_lines(fee_lines)
    for key in ("1p", "2p", "bt"):
        display_key = f"price_{key}"
        quote = quotes.get(key)
        if quote:
            put(f"{display_key}", quote["amount"], quote["line"], None)
            put(f"{display_key}_display", quote["text"], quote["line"], None)
    amount = (
        (quotes.get("1p") or {}).get("amount")
        or (quotes.get("2p") or {}).get("amount")
        or (quotes.get("bt") or {}).get("amount")
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
        value = card_weight_kg(card_fields) if key == "weight_kg" else card_fields.get(key)
        if value not in (None, ""):
            card_ev = card_evidence.get(key) or {}
            put(key, value, card_ev.get("line"), card_ev.get("score"))

    return fields, evidence
