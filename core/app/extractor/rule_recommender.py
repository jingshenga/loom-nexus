"""Mine repeatable alias candidates from manual extraction corrections."""
import hashlib
import json
import re
from collections import defaultdict

_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_-]{1,15}")
_GENERIC = {
    "价格", "资费", "收费", "服务", "项目", "禁忌", "体重", "身高", "年龄",
    "胸围", "罩杯", "分钟", "小时", "现金", "定金", "套餐", "元",
}
_FIELD_TARGETS = {
    "price_1p_text": ("tariff_alias", "1p"),
    "price_2p_text": ("tariff_alias", "2p"),
    "bust_size": ("bust_alias", None),
    "age_years": ("age_alias", None),
    "weight_kg": ("weight_alias", None),
    "restriction_text": ("restriction_alias", None),
    "fixed_intro_terms": ("restriction_alias", None),
    "service_text": ("service_alias", None),
    "features_text": ("service_alias", None),
}


def candidate_key(field_name: str, raw_pattern: str, target_value: str) -> str:
    payload = "\0".join((field_name, raw_pattern.casefold(), target_value.casefold()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _json_value(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return value


def _target(field_name, value):
    category, fixed = _FIELD_TARGETS[field_name]
    if fixed:
        return category, fixed
    value = _json_value(value)
    if field_name == "bust_size":
        match = re.search(r"[A-I]", str(value or "").upper())
        return (category, f"胸围{match.group(0)}") if match else (None, None)
    if field_name == "age_years":
        match = re.search(r"\d{1,2}", str(value or ""))
        return (category, f"年龄{match.group(0)}岁") if match else (None, None)
    if field_name == "weight_kg":
        match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
        return (category, f"体重{match.group(0)}kg") if match else (None, None)
    if isinstance(value, list):
        value = "、".join(str(item) for item in value if item)
    value = str(value or "").strip()
    return (category, value[:64]) if value and value.lower() != "null" else (None, None)


def _raw_patterns(snippet, covered):
    if not snippet:
        return []
    found = []
    for match in _TOKEN.finditer(str(snippet)):
        token = match.group(0).strip()
        for known in sorted((term for term in covered if len(term) > 1), key=len, reverse=True):
            token = token.replace(known, "")
        for generic in sorted(_GENERIC, key=len, reverse=True):
            token = token.replace(generic, "")
        if len(token) < 2 or token in _GENERIC or token.casefold() in covered:
            continue
        if token not in found:
            found.append(token)
    return found[:3]


def cluster_corrections(rows, covered=(), dismissed=(), min_frequency=2):
    """Group diffs by field, uncovered source phrase, and corrected target."""
    covered_set = {str(term).casefold() for term in covered if term}
    dismissed_set = set(dismissed)
    groups = defaultdict(list)
    for row in rows:
        field_name = str(row.get("field_name") or "")
        if field_name not in _FIELD_TARGETS:
            continue
        category, target_action = _target(field_name, row.get("new_value"))
        if not category or not target_action:
            continue
        for phrase in _raw_patterns(row.get("raw_snippet"), covered_set):
            key = candidate_key(field_name, phrase, target_action)
            if key not in dismissed_set:
                groups[(field_name, phrase, target_action, category)].append(row)
    candidates = []
    for (field_name, phrase, target, category), samples in groups.items():
        if len(samples) < min_frequency:
            continue
        examples = []
        for row in samples:
            context = {
                "listing_id": row.get("listing_id"),
                "nickname": row.get("nickname"),
                "raw_snippet": row.get("raw_snippet"),
                "before": row.get("old_value"),
                "after": row.get("new_value"),
            }
            if context not in examples:
                examples.append(context)
            if len(examples) == 3:
                break
        candidates.append({
            "candidate_key": candidate_key(field_name, phrase, target),
            "field_name": field_name, "raw_pattern": phrase,
            "category": category, "target_action": target,
            "frequency": len(samples), "confidence": round(min(0.99, 0.55 + 0.1 * len(samples)), 2),
            "status": "candidate", "examples": examples,
        })
    return sorted(candidates, key=lambda item: (-item["frequency"], item["field_name"], item["raw_pattern"]))
