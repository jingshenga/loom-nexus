"""Deterministic rule matching: gate, field extraction, lifecycle.

Operates on the rule config JSON stored in ingest_rules.config_json:

    {
      "gate": {"require_any_keywords": [...], "blocked_sender_peer_ids": [...]},
      "extract": {
        "nickname": ["<regex with one capture group>", ...],
        "username": [...],
        "price": [...],
        "district": [...],
        "currency_map": {"<pattern>": "CNY", ...}
      },
      "ocr": {"enabled": true, "trigger_when_missing": ["price"]},
      "lifecycle_terms": ["已售", ...]
    }

Pure functions only — no I/O, trivially unit-testable, shared by the
live pipeline and the dry-run sandbox.
"""
import re

FIELDS = ("nickname", "username", "district")


def gate_pass(rule: dict, text: str, sender_peer_id) -> tuple:
    """Return (ok, reason). Empty keyword list means no keyword gate."""
    gate = (rule or {}).get("gate") or {}
    keywords = gate.get("require_any_keywords") or []
    if keywords and not any(k in text for k in keywords if k):
        return False, "gate_keywords"
    blocked = gate.get("blocked_sender_peer_ids") or []
    if sender_peer_id is not None and int(sender_peer_id) in [int(x) for x in blocked]:
        return False, "gate_sender"
    return True, ""


def hit_lifecycle(rule: dict, text: str):
    """Return the matched lifecycle term, or None."""
    for term in (rule or {}).get("lifecycle_terms") or []:
        if term and term in text:
            return term
    return None


def _first_match(patterns, text):
    for pattern in patterns or []:
        found = re.search(pattern, text)
        if found and found.groups():
            value = found.group(1).strip()
            if value:
                return value
        elif found and found.group(0).strip():
            return found.group(0).strip()
    return None


def _match_currency(currency_map: dict, text: str):
    for pattern, code in (currency_map or {}).items():
        if re.search(pattern, text):
            return code
    return None


def extract_fields(rule: dict, text: str, src: str = "regex") -> tuple:
    """Return (fields, evidence) where evidence records the source of
    each extracted value: {"price": {"value": 888, "src": "regex"}}.
    `src` labels the provenance of this pass ("regex" or "ocr")."""
    cfg = (rule or {}).get("extract") or {}
    fields = {}
    evidence = {}

    for name in FIELDS:
        value = _first_match(cfg.get(name), text)
        if value:
            if name in {"nickname", "district"}:
                value = value.strip().lstrip("#").strip()
            # usernames are always stored bare ("abaoggio"); a pattern
            # that captures the "@" must not leak into the stored value
            if name == "username":
                value = value.lstrip("@")
            fields[name] = value
            evidence[name] = {"value": value, "src": src}

    price_raw = _first_match(cfg.get("price"), text)
    if price_raw is not None:
        try:
            amount = float(price_raw)
            # optional per-rule scaling: "price_multiply": 100 turns a
            # unit quote like 6P into yuan (600)
            multiplier = cfg.get("price_multiply")
            if isinstance(multiplier, (int, float)) and multiplier:
                amount = amount * multiplier
            fields["price_amount"] = amount
            evidence["price_amount"] = {"value": amount, "src": src}
        except ValueError:
            pass

    currency = _match_currency(cfg.get("currency_map"), text)
    if currency:
        fields["price_currency"] = currency
        evidence["price_currency"] = {"value": currency, "src": src}

    return fields, evidence


def merge_extraction(base_fields, base_evidence, ocr_fields, ocr_evidence):
    """Fill only the missing keys from the OCR pass; existing regex
    evidence is never overwritten (regex beats OCR)."""
    merged = dict(base_fields)
    merged_ev = dict(base_evidence)
    for key, value in ocr_fields.items():
        if key not in merged:
            merged[key] = value
            merged_ev[key] = ocr_evidence.get(key, {"value": value, "src": "ocr"})
    return merged, merged_ev
