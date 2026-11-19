"""Channel extraction profile: LLM-assisted structured extraction.

Per-source "extraction profiles" replace brittle hand-written regex
for bound channels: an admin triggers a learning pass over 10-20 raw
message samples, the model summarizes the channel's formatting habits
(price syntax like 8c / 800 / 1P800, body-data markers, service
terms, restriction tags) into a persisted JSON profile with a few
representative few-shot examples; afterwards the pipeline parses new
messages against that profile straight into the standard schema
(profile / tariffs / services / restrictions).

Pure helpers (prompt building, response parsing, merging) are kept
separate from I/O so they are trivially unit-testable; every LLM call
runs in a worker thread and any failure degrades to "no profile
result" — regex/OCR extraction is never blocked by the model.
"""
import asyncio
import json
import logging
import re

from app import sqlres

logger = logging.getLogger("nexus-core.profile-extract")

SQL_SAMPLES = sqlres.load("profile_samples.sql")
SQL_PROFILE_SET = sqlres.load("source_profile_set.sql")

# canonical keys the per-message extraction schema fills
CANONICAL_KEYS = (
    "nickname", "username", "district", "price_amount", "price_currency",
    "age_years", "height_cm", "weight_kg", "bust_size",
)
LIST_KEYS = ("price_1p_text", "price_2p_text", "cash_text", "price_text",
             "service_text", "restriction_text")

_LEARN_SYSTEM = (
    "你是频道消息排版分析器。分析同一频道的历史消息样本，总结该频道的排版习惯，"
    "输出严格的 JSON（不要 markdown 围栏、不要多余文字），结构：\n"
    '{"version": 1, "style_notes": "一句话概括排版风格", '
    '"price_patterns": ["该频道出现的价格写法，如 1P800 / 8张 / 0.8k"], '
    '"field_markers": {"艺名": "艺名：", "价格": "课费：", "城区": "地区："}, '
    '"few_shot": [{"text": "原样截取的一段代表性消息(可截断至150字)", '
    '"extracted": {"nickname": "…", "price_amount": 800, "district": "…"}}]}\n'
    "few_shot 给 2~3 个。若某类信息频道从不出现，对应字段留空数组或省略。"
)

_EXTRACT_SYSTEM = (
    "你是卡片信息抽取器。根据给定频道的排版画像，把消息解析为严格的 JSON"
    "（不要 markdown 围栏、不要多余文字），结构：\n"
    '{"nickname": "艺名或null", "username": "@句柄或null", "district": "城区或null", '
    '"price_amount": 数字或null, "price_currency": "CNY", '
    '"age_years": 数字或null, "height_cm": 数字或null, "weight_kg": 数字或null, '
    '"bust_size": "罩杯或null", '
    '"tariffs": {"p1": ["单人价原句"], "p2": ["双人价原句"], '
    '"cash_note": ["现金要求原句"], "raw_fees": ["其他资费原句"]}, '
    '"services": ["服务/特色标签"], "restrictions": ["禁忌标签，如 禁无套"]}\n'
    "消息里没有的信息一律 null 或空数组，禁止编造。价格按频道习惯换算成数字"
    "（如 1P800 → 800、8张/8c/8米 → 800、0.8k → 800）。"
)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def build_learn_messages(samples: list) -> list:
    """Learning prompt: system contract + numbered channel samples."""
    numbered = "\n".join(
        f"[{index + 1}] {str(text).strip()[:400]}"
        for index, text in enumerate(samples)
    )
    return [
        {"role": "system", "content": _LEARN_SYSTEM},
        {"role": "user", "content": f"频道消息样本：\n{numbered}"},
    ]


def build_extract_messages(text: str, profile: dict) -> list:
    """Per-message prompt: channel profile as context + the raw text."""
    notes = profile.get("style_notes") or ""
    markers = json.dumps(
        profile.get("field_markers") or {}, ensure_ascii=False,
    )
    patterns = "、".join(profile.get("price_patterns") or []) or "无特别写法"
    shots = ""
    for shot in (profile.get("few_shot") or [])[:3]:
        sample_text = str(shot.get("text") or "").strip()[:200]
        extracted = json.dumps(
            shot.get("extracted") or {}, ensure_ascii=False,
        )
        shots += f"\n消息：{sample_text}\n输出：{extracted}"
    user = (
        f"频道排版画像：{notes}\n"
        f"价格写法：{patterns}\n"
        f"字段标记：{markers}\n"
        f"示例：{shots or '（无）'}\n\n"
        f"待解析消息：\n{str(text).strip()[:1500]}"
    )
    return [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_llm_json(raw: str):
    """Strip markdown fences and parse the model's JSON answer; None on
    any failure (model output is untrusted)."""
    if not raw:
        return None
    candidate = (raw or "").strip()
    fence = _FENCE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    else:
        brace = candidate.find("{")
        end = candidate.rfind("}")
        if brace >= 0 and end > brace:
            candidate = candidate[brace:end + 1]
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_number(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value):
    number = _as_number(value)
    return int(number) if number is not None else None


def coerce_parsed(parsed: dict) -> dict:
    """Normalize the model's answer into the canonical schema; unknown
    keys are dropped, values that cannot be coerced degrade to None."""
    clean: dict = {}
    clean["nickname"] = parsed.get("nickname") or None
    clean["username"] = parsed.get("username") or None
    clean["district"] = parsed.get("district") or None
    price = _as_number(parsed.get("price_amount"))
    clean["price_amount"] = price if price and price > 0 else None
    # no default here: a model-omitted currency must not count as an
    # extracted value; the pipeline falls back to CNY when storing
    clean["price_currency"] = (
        str(parsed.get("price_currency")).strip()[:16]
        if parsed.get("price_currency") else None
    )
    clean["age_years"] = _as_int(parsed.get("age_years"))
    clean["height_cm"] = _as_int(parsed.get("height_cm"))
    weight = _as_number(parsed.get("weight_kg"))
    clean["weight_kg"] = round(weight, 1) if weight else None
    clean["bust_size"] = parsed.get("bust_size") or None

    tariffs = parsed.get("tariffs") if isinstance(parsed.get("tariffs"), dict) else {}
    clean["tariffs"] = {
        "p1": [str(x) for x in (tariffs.get("p1") or []) if str(x).strip()][:6],
        "p2": [str(x) for x in (tariffs.get("p2") or []) if str(x).strip()][:6],
        "cash_note": [str(x) for x in (tariffs.get("cash_note") or []) if str(x).strip()][:4],
        "raw_fees": [str(x) for x in (tariffs.get("raw_fees") or []) if str(x).strip()][:8],
    }
    clean["services"] = [str(x).strip()[:20] for x in (parsed.get("services") or []) if str(x).strip()][:16]
    clean["restrictions"] = [str(x).strip()[:20] for x in (parsed.get("restrictions") or []) if str(x).strip()][:12]
    return clean


def merge_profile_result(fields: dict, evidence: dict, parsed: dict) -> tuple:
    """LLM result joins the extraction output. Canonical fields only
    fill keys the regex/OCR passes missed (deterministic pass wins);
    the structured schema extras land in evidence tagged src="llm" so
    the listings API surfaces them as attributes. Returns (fields,
    evidence, filled_keys)."""
    merged = dict(fields)
    merged_ev = dict(evidence)
    filled = []

    def put(key, value):
        if value in (None, ""):
            return
        if key not in merged:
            merged[key] = value
            merged_ev[key] = {"value": value, "src": "llm"}
            filled.append(key)
        else:
            # the deterministic pass owns the value; keep its evidence
            # intact and just note the model agrees with it
            entry = merged_ev.get(key)
            if isinstance(entry, dict):
                entry.setdefault("llm_corroboration", value)
            else:
                merged_ev[key] = {"value": value, "src": "llm", "corroboration": True}

    for key in CANONICAL_KEYS:
        put(key, parsed.get(key))
    price_currency = parsed.get("price_currency")
    if price_currency and "price_currency" not in merged:
        put("price_currency", price_currency)

    tariffs = parsed.get("tariffs") or {}
    extras = {
        "price_1p_text": tariffs.get("p1") or [],
        "price_2p_text": tariffs.get("p2") or [],
        "cash_text": tariffs.get("cash_note") or [],
        "price_text": tariffs.get("raw_fees") or [],
        "service_text": parsed.get("services") or [],
        "restriction_text": parsed.get("restrictions") or [],
    }
    for key, values in extras.items():
        if values and key not in merged_ev:
            merged_ev[key] = {"value": values, "src": "llm"}
            filled.append(key)
    return merged, merged_ev, filled


def has_content(parsed: dict) -> bool:
    """True when the parsed answer carries at least one usable value.
    price_currency is excluded — coerce fills it with a default, so it
    never counts as extracted content on its own."""
    clean = coerce_parsed(parsed or {})
    canonical = any(
        clean.get(key) not in (None, "", [])
        for key in CANONICAL_KEYS if key != "price_currency"
    )
    extras = any(clean["tariffs"].values()) or clean["services"] or clean["restrictions"]
    return bool(canonical or extras)


async def learn_channel_profile(pool, chat_fn, cfg: dict, source_id: int,
                                sample_count: int = 18) -> dict:
    """Learning pass: sample 10-20 channel messages, ask the model for
    the channel's formatting profile, persist it on the source row.
    `chat_fn` is llm.chat (or a test double) with signature
    (cfg, messages, timeout). Raises ValueError with a friendly
    message on any failure."""
    rows = await pool.fetch(SQL_SAMPLES, source_id, max(10, min(sample_count, 20)))
    samples = [str(r["raw_text"]).strip()[:400] for r in rows]
    if len(samples) < 5:
        raise ValueError(f"本地样本不足（{len(samples)} 条，需至少 5 条有内容的消息）；请先回填历史")
    messages = build_learn_messages(samples)
    answer = await asyncio.to_thread(chat_fn, cfg, messages, 90)
    profile = parse_llm_json(answer)
    if not profile:
        raise ValueError("模型返回无法解析为 JSON，请重试或更换模型")
    profile.setdefault("version", 1)
    profile["sample_count"] = len(samples)
    profile["few_shot"] = [
        shot for shot in (profile.get("few_shot") or [])
        if isinstance(shot, dict) and shot.get("text")
    ][:3]
    await pool.fetch(
        SQL_PROFILE_SET, source_id, json.dumps(profile, ensure_ascii=False),
    )
    return profile


async def extract_with_profile(chat_fn, text: str, profile: dict):
    """One schema-extraction pass for a message against its channel
    profile. Returns the coerced dict, or None on any failure — the
    pipeline keeps its regex/OCR result untouched in that case."""
    try:
        messages = build_extract_messages(text, profile or {})
        answer = await asyncio.to_thread(chat_fn, messages, 60)
    except Exception as exc:
        logger.warning("profile extraction failed (degraded): %s", exc)
        return None
    parsed = parse_llm_json(answer)
    if not parsed or not has_content(parsed):
        return None
    return coerce_parsed(parsed)


def profile_ready(profile, llm_cfg: dict) -> bool:
    """Profile gating for the pipeline: a learned profile plus a
    configured model endpoint."""
    return bool(profile) and bool(llm_cfg.get("base_url") and llm_cfg.get("model"))
