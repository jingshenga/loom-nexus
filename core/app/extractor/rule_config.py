"""Bounded validation for rule configurations used by new rule versions."""
import math
import re


MAX_RULE_TEXT = 20_000
MAX_PATTERN_LENGTH = 256
MAX_PATTERNS = 64
PARSER_KEYS = {
    "ast_rules_v1", "on_duty_v1", "profile_collection_v2",
    "bot_collection_v1", "checkin_reverse_v2",
}
PARSER_BY_TYPE = {
    "listing": "ast_rules_v1",
    "on_duty": "on_duty_v1",
    "profile_collection": "profile_collection_v2",
    "bot_collection": "bot_collection_v1",
    "checkin_reverse": "checkin_reverse_v2",
}
RULE_TYPES = {
    "listing", "on_duty", "profile_collection", "bot_collection", "checkin_reverse",
}
MATCH_MODES = {
    "legacy_single_selected", "exclusive_first_valid", "fallback_on_no_match",
    "fallback_on_no_structure", "additive",
}
_NESTED_REPEAT = re.compile(r"\([^)]*(?:[+*]|\{\d+(?:,\d*)?\})[^)]*\)(?:[+*]|\{\d+(?:,\d*)?\})")
_REPEATED_ALTERNATION = re.compile(r"\([^)]*\|[^)]*\)(?:[+*]|\{\d+(?:,\d*)?\})")
_PRIVATE_CONFIG_KEYS = {
    "api_key", "api_hash", "api_id", "session", "session_file", "session_string",
    "phone", "password", "secret", "token", "bot_token", "auth_token",
    "authorization", "access_hash",
}


def _has_private_config_key(value) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).strip().lower() in _PRIVATE_CONFIG_KEYS or _has_private_config_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_private_config_key(child) for child in value)
    return False


def validate_rule_config(rule_type: str, config: dict, *, strict: bool = True) -> list[str]:
    """Return validation errors without executing any rule against input text."""
    errors = []
    if rule_type not in RULE_TYPES:
        return ["unknown rule_type"]
    if not isinstance(config, dict):
        return ["config must be an object"]
    if _has_private_config_key(config):
        errors.append("rule config must not contain Telegram account credentials or secrets")

    parser_key = config.get("parser_key")
    if parser_key is not None and parser_key not in PARSER_KEYS:
        errors.append("parser_key is not in the approved registry")
    elif parser_key is not None and parser_key != PARSER_BY_TYPE[rule_type]:
        errors.append("parser_key does not match rule_type")
    if rule_type == "listing":
        gate = config.get("gate", {})
        extract = config.get("extract", {})
        ocr = config.get("ocr", {})
        if not isinstance(gate, dict) or not isinstance(extract, dict) or not isinstance(ocr, dict):
            return errors + ["gate, extract and ocr must be objects"]
        keywords = gate.get("require_any_keywords", [])
        if not isinstance(keywords, list) or len(keywords) > MAX_PATTERNS or not all(
            isinstance(item, str) and 0 < len(item) <= 128 for item in keywords
        ):
            errors.append("require_any_keywords must contain at most 64 strings of 1-128 chars")
        blocked = gate.get("blocked_sender_peer_ids", [])
        if not isinstance(blocked, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) for item in blocked
        ):
            errors.append("blocked_sender_peer_ids must be integers")

        pattern_count = 0
        for field in ("nickname", "username", "district", "price"):
            patterns = extract.get(field, [])
            if not isinstance(patterns, list):
                errors.append(f"extract.{field} must be a list")
                continue
            pattern_count += len(patterns)
            for pattern in patterns:
                if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_LENGTH:
                    errors.append(f"extract.{field} patterns must be 1-{MAX_PATTERN_LENGTH} chars")
                    continue
                if "(?<=" in pattern or "(?<!" in pattern or re.search(r"\\[1-9]", pattern):
                    errors.append(f"extract.{field} uses unsupported lookbehind/backreference")
                    continue
                if _NESTED_REPEAT.search(pattern) or _REPEATED_ALTERNATION.search(pattern):
                    errors.append(f"extract.{field} has an unsafe nested repetition")
                    continue
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"extract.{field} regex is invalid: {exc}")
        if pattern_count > MAX_PATTERNS:
            errors.append(f"at most {MAX_PATTERNS} field patterns are allowed")

        multiplier = extract.get("price_multiply")
        if multiplier is not None and (
            not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool)
            or not math.isfinite(multiplier) or multiplier <= 0 or multiplier > 1000
        ):
            errors.append("extract.price_multiply must be a finite number in (0,1000]")
        currency_map = extract.get("currency_map", {})
        if not isinstance(currency_map, dict):
            errors.append("extract.currency_map must be an object")
        else:
            if len(currency_map) > MAX_PATTERNS:
                errors.append(f"extract.currency_map allows at most {MAX_PATTERNS} patterns")
            for pattern, value in currency_map.items():
                if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_LENGTH:
                    errors.append("extract.currency_map keys must be regex strings up to 256 chars")
                elif not isinstance(value, str) or len(value) > 12:
                    errors.append("extract.currency_map values must be short strings")
                else:
                    if "(?<=" in pattern or "(?<!" in pattern or re.search(r"\\[1-9]", pattern):
                        errors.append("extract.currency_map uses unsupported lookbehind/backreference")
                        continue
                    if _NESTED_REPEAT.search(pattern) or _REPEATED_ALTERNATION.search(pattern):
                        errors.append("extract.currency_map has an unsafe nested repetition")
                        continue
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(f"extract.currency_map regex is invalid: {exc}")

        lifecycle = config.get("lifecycle_terms", [])
        if not isinstance(lifecycle, list) or len(lifecycle) > MAX_PATTERNS or not all(
            isinstance(item, str) and 0 < len(item) <= 64 for item in lifecycle
        ):
            errors.append("lifecycle_terms must contain at most 64 strings of 1-64 chars")
        if not isinstance(ocr.get("enabled", False), bool):
            errors.append("ocr.enabled must be boolean")
        trigger = ocr.get("trigger_when_missing", [])
        if not isinstance(trigger, list) or not all(
            isinstance(item, str) and item in {"price", "nickname", "username", "district", "description"}
            for item in trigger
        ):
            errors.append("ocr.trigger_when_missing contains an unsupported field")
        if strict and not any(extract.get(field) for field in ("nickname", "username", "price", "district")):
            errors.append("listing rule must extract at least one supported field")
    elif rule_type == "on_duty":
        required = ("sender_peer_id", "match_prefix", "update_mode", "identity_mode")
        if strict and any(not config.get(key) for key in required):
            errors.append("on_duty requires sender_peer_id, match_prefix, update_mode and identity_mode")
        if config.get("identity_mode") == "linked_listing_messages" and not config.get("target_channel_username"):
            errors.append("linked_listing_messages requires target_channel_username")
        if strict and config.get("identity_mode") not in {"direct_username_links", "linked_listing_messages"}:
            errors.append("unsupported on_duty identity_mode")
        if strict and config.get("update_mode") not in {"edited_snapshot", "replace_snapshot"}:
            errors.append("unsupported on_duty update_mode")
    elif rule_type == "profile_collection":
        limits = {
            "recent_window": (1, 10), "max_scan_messages": (10, 100),
            "max_photos": (1, 6), "max_videos": (0, 2),
            "intro_min_chars": (1, 500), "intro_min_lines": (1, 30),
            "pinned_followup_messages": (1, 5),
        }
        for key, (minimum, maximum) in limits.items():
            value = config.get(key)
            if strict and (not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum):
                errors.append(f"{key} must be an integer in [{minimum},{maximum}]")
        for key in ("channel_any_sender_matches", "skip_video_when_photo_found", "exclude_avatar"):
            if strict and config.get(key) is not True:
                errors.append(f"{key} must be true")
    elif rule_type == "checkin_reverse":
        source = config.get("source_username")
        if strict and (not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", source)):
            errors.append("source_username must be a public Telegram username")
        if strict and config.get("collection_mode", "checkin_source_reverse") != "checkin_source_reverse":
            errors.append("unsupported checkin_reverse collection_mode")
    elif rule_type == "bot_collection":
        if strict and set(config) - {"parser_key", "precheck_enabled", "allow_pending_cards",
                                     "reuse_verified_days", "reuse_no_bot_days"}:
            errors.append("Bot 规则只接受前置条件与结果复用设置")
        for key in ("precheck_enabled", "allow_pending_cards"):
            if key in config and not isinstance(config[key], bool):
                errors.append(f"Bot 配置 {key} 必须是布尔值")
        for key in ("reuse_verified_days", "reuse_no_bot_days"):
            if key in config and (type(config[key]) is not int or not 0 <= config[key] <= 365):
                errors.append(f"Bot 配置 {key} 必须是 0 至 365 的整数")
    return errors


def validate_match_mode(mode: str) -> bool:
    return mode in MATCH_MODES
