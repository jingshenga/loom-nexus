"""Conservative adapters for persisted OCR card fields."""
import re
from decimal import Decimal, InvalidOperation


def _values(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if item is not None and str(item).strip()]
    if isinstance(value, dict):
        value = value.get("value") or value.get("text")
    return [str(value).strip()] if value is not None and str(value).strip() else []


def _line_evidence(key, evidence, *, ocr_id, engine_version, confidence):
    item = evidence.get(key) if isinstance(evidence, dict) else None
    if not isinstance(item, dict):
        item = {}
    return {
        "src": "ocr",
        "ocr_id": ocr_id,
        "engine_version": engine_version,
        "line": item.get("line"),
        "confidence": item.get("score", confidence),
        "source_field": key,
    }


def _handles(values):
    found = set()
    for value in values:
        value = value.strip()
        exact = re.fullmatch(r"@?([A-Za-z][A-Za-z0-9_]{4,31})", value)
        if exact:
            found.add(exact.group(1).casefold())
            continue
        for match in re.finditer(
            r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})(?=$|[/?#\s])"
            r"|@([A-Za-z][A-Za-z0-9_]{4,31})\b", value, re.I,
        ):
            found.add((match.group(1) or match.group(2)).casefold())
    return sorted(found)


def _prices(values):
    """Return labeled amounts with explicit currency/unit evidence only."""
    amounts = []
    pattern = re.compile(
        r"(?:价格|课费|费用|收费)\s*[:：=]?\s*"
        r"(人民币|RMB|CNY|[¥￥])?\s*"
        r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*"
        r"(人民币|RMB|CNY|元|块|[¥￥])?(?=$|[^\w])", re.I,
    )
    for value in values:
        for match in pattern.finditer(value):
            if not (match.group(1) or match.group(3)):
                continue
            try:
                amounts.append(Decimal(match.group(2).replace(",", "")))
            except InvalidOperation:
                continue
    return sorted(set(amounts))


def _district(values):
    """Require a location label and an administrative suffix."""
    results = set()
    for value in values:
        match = re.search(r"(?:地区|城区|地点|工作地|地址)\s*[:：=]\s*([^，,;；\n]+)", value)
        if not match:
            continue
        location = re.sub(r"\s+", "", match.group(1)).strip("。；;，,")
        if re.search(r"(?:市|区|县|镇|街道)$", location) and len(location) >= 3:
            results.add(location)
    return sorted(results)


def structured_ocr_candidate(fields, evidence, *, ocr_id, engine_version, confidence,
                             text_identity=""):
    """Map only labeled OCR fields with conservative semantics; retain ambiguity as evidence."""
    fields = fields if isinstance(fields, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    candidate = {"fields": {}, "evidence": {}, "conflicts": [], "clues": {}}

    def put(source_key, target_key, value):
        candidate["fields"][target_key] = value
        candidate["evidence"][target_key] = _line_evidence(
            source_key, evidence, ocr_id=ocr_id, engine_version=engine_version,
            confidence=confidence,
        )

    stage_names = _values(fields.get("stage_name"))
    if len(set(stage_names)) == 1:
        put("stage_name", "nickname", stage_names[0])
    elif len(set(stage_names)) > 1:
        candidate["conflicts"].append({"kind": "field", "field": "nickname", "source": "ocr_fields_json"})

    stage_clues = _values(fields.get("stage_name_candidate"))
    if stage_clues:
        candidate["clues"]["stage_name_candidate"] = {
            "values": sorted(set(stage_clues)),
            "evidence": _line_evidence("stage_name_candidate", evidence, ocr_id=ocr_id,
                                        engine_version=engine_version, confidence=confidence),
        }

    contact_values = _values(fields.get("contact_handles"))
    handles = _handles(contact_values)
    normalized_text_identity = (text_identity or "").strip().lstrip("@").casefold()
    if normalized_text_identity:
        if normalized_text_identity in handles:
            put("contact_handles", "username", normalized_text_identity)
        elif handles:
            candidate["conflicts"].append({
                "kind": "telegram_identity", "source": "telegram_text_vs_ocr_fields",
                "values": [normalized_text_identity, *handles],
                "evidence": _line_evidence("contact_handles", evidence, ocr_id=ocr_id,
                                            engine_version=engine_version, confidence=confidence),
            })
    elif len(handles) == 1:
        put("contact_handles", "username", handles[0])
        candidate["evidence"]["username"]["identity_validation"] = "single_ocr_handle_requires_human_review"
    elif len(handles) > 1:
        candidate["conflicts"].append({
            "kind": "telegram_identity", "source": "multiple_ocr_handles",
            "values": handles,
            "evidence": _line_evidence("contact_handles", evidence, ocr_id=ocr_id,
                                        engine_version=engine_version, confidence=confidence),
        })

    locations = _district(_values(fields.get("location_text")))
    if len(locations) == 1:
        put("location_text", "district", locations[0])
    elif len(locations) > 1:
        candidate["conflicts"].append({"kind": "field", "field": "district", "source": "multiple_ocr_locations", "values": locations})

    price_values = _values(fields.get("price_text"))
    prices = _prices(price_values)
    if len(prices) == 1:
        amount = prices[0]
        price = int(amount) if amount == amount.to_integral_value() else float(amount)
        put("price_text", "price_amount", price)
        candidate["fields"]["price_currency"] = "CNY"
        candidate["evidence"]["price_currency"] = _line_evidence(
            "price_text", evidence, ocr_id=ocr_id, engine_version=engine_version,
            confidence=confidence,
        )
    elif len(prices) > 1:
        candidate["conflicts"].append({
            "kind": "field", "field": "price_amount", "source": "multiple_labeled_ocr_prices",
            "values": [int(value) if value == value.to_integral_value() else float(value) for value in prices],
            "evidence": _line_evidence("price_text", evidence, ocr_id=ocr_id,
                                        engine_version=engine_version, confidence=confidence),
        })
        candidate["clues"]["price_text"] = {
            "values": price_values,
            "evidence": _line_evidence("price_text", evidence, ocr_id=ocr_id,
                                        engine_version=engine_version, confidence=confidence),
        }
    elif price_values:
        candidate["clues"]["price_text"] = {
            "values": price_values,
            "evidence": _line_evidence("price_text", evidence, ocr_id=ocr_id,
                                        engine_version=engine_version, confidence=confidence),
        }
    return candidate


def equivalent_field(field, left, right):
    if field == "username":
        return str(left or "").strip().lstrip("@").casefold() == str(right or "").strip().lstrip("@").casefold()
    if field == "price_amount":
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except InvalidOperation:
            return False
    return re.sub(r"\s+", "", str(left or "")).casefold() == re.sub(r"\s+", "", str(right or "")).casefold()
