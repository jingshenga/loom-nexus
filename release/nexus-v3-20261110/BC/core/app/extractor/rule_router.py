"""Pure, side-effect-free matcher and legacy/shadow comparison helpers."""
import hashlib
import json

from app.extractor import ast_rules
from app.extractor.rule_config import MAX_RULE_TEXT, validate_rule_config


def business_candidate_key(source_id, platform_msg_id, purpose, target_identity=""):
    """Stable key intentionally independent of rule/config version."""
    payload = json.dumps(
        [int(source_id), int(platform_msg_id), str(purpose), str(target_identity).lower()],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def config_hash(config):
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_candidate(binding, text, sender_peer_id=None):
    """Run a bounded listing matcher; never performs I/O or OCR."""
    rule_type = binding.get("rule_type", "listing")
    rule_id = binding.get("rule_id")
    version_id = binding.get("rule_version_id")
    config = binding.get("config_json") or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (TypeError, json.JSONDecodeError):
            config = None
    trace = {
        "rule_id": rule_id, "rule_version_id": version_id,
        "rule_version_known": version_id is not None,
        "rule_type": rule_type, "priority": int(binding.get("priority") or 0),
    }
    if rule_type != "listing":
        return {**trace, "supported": False, "matched": False,
                "reason": "worker_specific_matcher_not_run"}
    if not isinstance(config, dict):
        return {**trace, "supported": True, "matched": False, "reason": "invalid_config"}
    errors = validate_rule_config("listing", config, strict=False)
    if errors:
        return {**trace, "supported": True, "matched": False,
                "reason": "unsafe_or_invalid_config", "validation_errors": errors}

    bounded_text = (text or "")[:MAX_RULE_TEXT]
    ok, gate_reason = ast_rules.gate_pass(config, bounded_text, sender_peer_id)
    if not ok:
        return {**trace, "supported": True, "matched": False,
                "reason": gate_reason, "fields": {}, "evidence": {}, "lifecycle": None}
    fields, evidence = ast_rules.extract_fields(config, bounded_text)
    lifecycle = ast_rules.hit_lifecycle(config, bounded_text)
    matched = bool(fields) or lifecycle is not None
    missing = [name for name in ("nickname", "username", "price_amount", "district")
               if not fields.get(name)]
    return {
        **trace, "supported": True, "matched": matched,
        "reason": "match" if matched else "no_structure",
        "fields": fields, "evidence": evidence, "lifecycle": lifecycle,
        "identity": (fields.get("username") or "").strip().lower(),
        "missing_fields": missing,
        "ocr_would_be_considered": bool(
            (config.get("ocr") or {}).get("enabled")
            and any(name == "price" and not fields.get("price_amount")
                    for name in (config.get("ocr") or {}).get("trigger_when_missing", []))
            and not lifecycle
        ),
    }


def legacy_selected(bindings):
    """Mirror extract_claim_batch: highest bound priority, otherwise default."""
    active_listing = [b for b in bindings if b.get("rule_type") == "listing"
                      and b.get("is_active", True) and not b.get("is_default")]
    if active_listing:
        return sorted(active_listing, key=lambda b: (-int(b.get("priority") or 0), int(b.get("rule_id") or 0)))[0]
    defaults = [b for b in bindings if b.get("rule_type") == "listing"
                and b.get("is_active", True) and b.get("is_default")]
    return sorted(defaults, key=lambda b: int(b.get("rule_id") or 0))[0] if defaults else None


def _conflicts(matches):
    identities = {r.get("identity") for r in matches if r.get("identity")}
    conflicts = []
    if len(identities) > 1:
        conflicts.append({"kind": "telegram_identity", "values": sorted(identities)})
    fields = {key for result in matches for key in (result.get("fields") or {})}
    for key in sorted(fields):
        values = {json.dumps(result["fields"][key], ensure_ascii=False, sort_keys=True)
                  for result in matches if key in (result.get("fields") or {})}
        if len(values) > 1:
            conflicts.append({"kind": "field", "field": key, "values": sorted(values)})
    return conflicts


def resolve_group(bindings, text, sender_peer_id=None):
    """Evaluate every matcher before applying exclusive/fallback/additive policy."""
    ordered = sorted(bindings, key=lambda b: (-int(b.get("priority") or 0), int(b.get("rule_id") or 0)))
    if not ordered:
        return {"selected": None, "trace": [], "conflicts": [], "reason": "no_bindings"}
    modes = {b.get("mode", "legacy_single_selected") for b in ordered}
    if len(modes) != 1:
        return {"selected": None, "trace": [], "conflicts": [{"kind": "mode_mismatch"}],
                "reason": "conflicting_match_modes"}
    mode = next(iter(modes))
    if mode in {"fallback_on_no_match", "fallback_on_no_structure"}:
        ordered = sorted(ordered, key=lambda b: (
            int(b.get("fallback_order") or 0),
            -int(b.get("priority") or 0), int(b.get("rule_id") or 0),
        ))
    if mode == "legacy_single_selected":
        evaluated = [evaluate_candidate(ordered[0], text, sender_peer_id)]
    else:
        evaluated = [evaluate_candidate(b, text, sender_peer_id) for b in ordered]
    matched = [r for r in evaluated if r.get("supported") and r.get("matched")]
    conflicts = _conflicts(matched)
    if conflicts:
        return {"selected": None, "trace": evaluated, "conflicts": conflicts,
                "mode": mode, "reason": "candidate_conflict"}

    selected = None
    reason = "no_match"
    if mode in {"legacy_single_selected", "exclusive_first_valid", "fallback_on_no_match"}:
        selected = next(iter(matched), None)
        if selected:
            reason = "first_valid_match" if mode != "legacy_single_selected" else "legacy_priority_selection"
    elif mode == "fallback_on_no_structure":
        selected = next((r for r in matched if r.get("fields")), None)
        reason = "first_structured_match" if selected else "no_structure"
    elif mode == "additive":
        if matched:
            merged_fields, merged_evidence = {}, {}
            for result in matched:
                for key, value in result.get("fields", {}).items():
                    merged_fields.setdefault(key, value)
                    if key in result.get("evidence", {}):
                        merged_evidence.setdefault(key, result["evidence"][key])
            selected = {
                "rule_ids": [r.get("rule_id") for r in matched],
                "fields": merged_fields, "evidence": merged_evidence,
                "identity": next((r.get("identity") for r in matched if r.get("identity")), ""),
                "lifecycle": next((r.get("lifecycle") for r in matched if r.get("lifecycle")), None),
            }
            reason = "additive_merge"
    else:
        return {"selected": None, "trace": evaluated, "conflicts": [],
                "mode": mode, "reason": "unsupported_mode"}
    return {"selected": selected, "trace": evaluated, "conflicts": [],
            "mode": mode, "reason": reason}


def shadow_compare(bindings, text, sender_peer_id=None, legacy_config=None):
    """Return the legacy outcome and shadow prediction without side effects."""
    legacy_binding = legacy_selected(bindings)
    if legacy_config is not None:
        legacy_binding = {"rule_id": None, "rule_type": "listing", "priority": 0,
                         "config_json": legacy_config, "rule_version_id": None}
    actual = evaluate_candidate(legacy_binding, text, sender_peer_id) if legacy_binding else None
    supported = [b for b in bindings if b.get("rule_type") == "listing"
                 and not b.get("is_default", False) and b.get("is_active", True)]
    if not supported:
        supported = [b for b in bindings if b.get("rule_type") == "listing"
                     and b.get("is_default", False) and b.get("is_active", True)]
    groups = {}
    for binding in supported:
        key = (binding.get("purpose") or "listing", binding.get("match_group") or "default")
        groups.setdefault(key, []).append(binding)
    predictions = []
    for (purpose, match_group), group in sorted(groups.items()):
        prediction = resolve_group(group, text, sender_peer_id)
        predictions.append({"purpose": purpose, "match_group": match_group, **prediction})
    predicted_identity = {
        item["purpose"]: (item.get("selected") or {}).get("identity", "")
        for item in predictions
    }
    actual_identity = (actual or {}).get("identity", "")
    actual_fields = (actual or {}).get("fields") or {}
    changed = any(
        item.get("conflicts") or (
            item["purpose"] == "listing" and (
                (item.get("selected") or {}).get("identity", "") != actual_identity
                or (item.get("selected") or {}).get("fields", {}) != actual_fields
                or bool(item.get("selected")) != bool(actual and actual.get("matched"))
            )
        ) for item in predictions
    )
    return {"actual_legacy": actual, "shadow_predictions": predictions,
            "identity_by_purpose": predicted_identity, "changed": changed}
