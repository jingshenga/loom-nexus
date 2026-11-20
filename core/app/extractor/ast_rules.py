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

Extraction is tolerant by design: before rule regexes run the text is
normalized (full-width marks folded, money slang like 0.8k / 8张 / 8米
rewritten to plain yuan), and every pattern that misses the whole text
is retried against tolerant chunks (bracket-peeled lines, key/value
halves, label+value pairs split across lines). Chunking can only add
hits — a pattern that already matches the whole text keeps its result.
"""
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock

from flashtext import KeywordProcessor

FIELDS = ("nickname", "username", "district")

# ---------------------------------------------------------------------------
# pre-normalization: money slang & full/half-width marks
# ---------------------------------------------------------------------------

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad]")

# keycap digit emoji (1️⃣ 2️⃣ 3️⃣ …) fold back to plain digits; pointing
# arrows become spaces; prohibition emojis fold to the character 禁 so
# restriction patterns keep matching emoji-decorated messages
_KEYCAP_DIGIT = re.compile(r"([0-9])\uFE0F?\u20E3")
_POINTING_MARKS = re.compile(r"[👉➡▶⏩]\uFE0F?")
_PROHIBITION_MARKS = re.compile(r"[🈲🚫⛔]")

# Only numeric context needs a grammar. Units themselves live in the trie.
_STATIC_MONEY = {
    "章": 100, "张": 100, "米": 100, "c": 100, "C": 100,
    "千": 1000, "k": 1000, "K": 1000, "万": 10000, "w": 10000, "W": 10000,
}
_MONEY_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*")
_COMPACT_DURATION = re.compile(r"\d{1,3}\s*(?:分钟|分|min\b)", re.I)
_BUST_LABEL = r"(?:胸围?|罩杯|(?<![A-Za-z])cup|大?熊|🐻\ufe0f?)"
_BUST_MODIFIERS = r"(?:(?:天然|自然|原装|真)\s*)?"
_BUST_PREFIX = re.compile(rf"(?:{_BUST_LABEL}\s*[:=]?\s*{_BUST_MODIFIERS}|天然\s*)$", re.I)
_HEIGHT_PREFIX = re.compile(r"(?:身高|净高|身量|个头)\s*[:：]?\s*$")


def _rewrite_money_units(text: str, processor: KeywordProcessor) -> str:
    numbers = iter(_MONEY_NUMBER.finditer(text))
    number = next(numbers, None)
    parts = []
    cursor = 0
    for (unit, factor, dynamic), start, end in _spans(processor, text):
        while number and number.end() < start:
            number = next(numbers, None)
        if not number:
            break
        if number.end() != start:
            continue
        following = text[end:end + 1]
        compact_duration = _COMPACT_DURATION.match(text, end) is not None
        if following.isdigit() and not compact_duration:
            continue
        if unit[-1] in _ASCII_WORD_EDGE and following in _ASCII_WORD_EDGE and not compact_duration:
            continue
        if (dynamic or unit in "张章米") and following and following in "照片图票券卡桌":
            continue
        prefix = text[max(0, number.start() - 24):number.start()]
        if unit == "米" and (_HEIGHT_PREFIX.search(prefix) or following == "."):
            continue
        if (dynamic or unit.lower() == "c") and (
            _BUST_PREFIX.search(prefix) or text[end:].lstrip().startswith(("罩杯", "杯"))
        ):
            continue
        digits = number[1]
        if not dynamic and unit.lower() == "k" and len(digits) == 2 and digits.isdigit() and int(digits) >= 11:
            factor = 100
        amount = format(Decimal(digits) * factor, "f")
        if "." in amount:
            amount = amount.rstrip("0").rstrip(".")
        parts.extend((text[cursor:number.start()], amount))
        if compact_duration:
            parts.append(" ")
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def money_normalize(text: str) -> str:
    return _rewrite_money_units(text, _BASE_MONEY)


def _fold_input(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _ZERO_WIDTH.sub("", text)
    text = _KEYCAP_DIGIT.sub(r"\1", text)
    text = _POINTING_MARKS.sub(" ", text)
    text = _PROHIBITION_MARKS.sub("禁", text)
    return re.sub(r"[ \t]+", " ", text).strip()


_SLANG_UNIT = "[" + "".join(_STATIC_MONEY) + "]"
def normalize_input(text: str) -> str:
    """Fold width, emoji and money slang without changing line boundaries."""
    snapshot = _snapshot()
    folded = snapshot.aliases.replace(_fold_input(text))
    return _rewrite_money_units(re.sub(r"(?<=\d),(?=\d)", "", folded), snapshot.money)


# Body fields are shared by text rules and OCR, which keeps the original line as evidence.
_AGE_LABEL = re.compile(r"(年龄|年纪|出生(?:年份)?)\s*[:=]?\s*(\d{4}|\d{1,2})(?![\d.])\s*([岁年])?(?![岁年\dA-Za-z.])")
_DIRECT_AGE = re.compile(r"^([0-9]{1,2})\s*岁(?=$|[\s/|,;·-])")
_AGE_YEAR = r"(?P<year>\d{4}|\d{2})"
_HEIGHT_TOKEN = r"(?:(?:身高|净高)\s*[:=]?\s*)?1[4-9]\d\s*(?:cm|厘米)?"
_PROFILE_SEPARATOR = r"(?:\s*[/|,;·-]\s*|\s+)"
_PROFILE_YEAR = (
    re.compile(rf"^\s*{_AGE_YEAR}\s*年(?:出生)?\s*$"),
    re.compile(rf"^\s*{_AGE_YEAR}\s*年\s*{_HEIGHT_TOKEN}(?=$|[\s/|,;·-])", re.I),
    re.compile(rf"^\s*(?:身高|净高)?\s*[:=]?\s*1[4-9]\d\s*(?:cm|厘米)\s*{_AGE_YEAR}年?(?=$|[\s/|,;·-])", re.I),
    re.compile(rf"^\s*{_AGE_YEAR}(?:\s*年)?{_PROFILE_SEPARATOR}{_HEIGHT_TOKEN}(?=$|[\s/|,;·-])", re.I),
    re.compile(rf"^\s*{_HEIGHT_TOKEN}{_PROFILE_SEPARATOR}{_AGE_YEAR}(?:\s*年)?(?=$|[\s/|,;·-])", re.I),
)
_WEIGHT_VALUE = r"(\d+(?:\.\d+)?)\s*(kg|公斤|斤)?"
_WEIGHT_LABEL = re.compile(
    rf"体重\s*[:=]?\s*{_WEIGHT_VALUE}"
    r"(?![\d.A-Za-z]|\s*(?:磅|千克|克|lbs?\b|/\s*斤))",
    re.I,
)
_WEIGHT_TOKEN = re.compile(_WEIGHT_VALUE, re.I)
_CUP = r"(?:([A-I]\+?)\s*\d{2}|(?:\d{2}\s*)?([A-I]\+?))"
_BUST_BEFORE = re.compile(rf"(?:{_BUST_LABEL}|天然)\s*[:=]?\s*{_BUST_MODIFIERS}{_CUP}(?![A-Za-z0-9+])", re.I)
_BUST_AFTER = re.compile(rf"(?<![A-Za-z0-9]){_CUP}\s*(?:罩杯|杯)", re.I)
_BUST_STANDALONE = re.compile(r"(?:([A-I])\s*\d{2}|\d{2}\s*([A-I]))", re.I)


def _birth_age(year: str) -> int | None:
    value = int(year)
    if len(year) == 2:
        if value <= 26:
            value += 2000
        elif value >= 70:
            value += 1900
        else:
            return None
    age = 2026 - value
    return age if 18 <= age <= 45 else None


def find_age(lines: list[str]) -> tuple[int, int] | None:
    """Return the source line index and age, preferring labeled ages."""
    folded_lines = [_apply_dynamic_aliases(_fold_input(line)) for line in lines]
    for index, line in enumerate(folded_lines):
        for match in _AGE_LABEL.finditer(line):
            label, number, unit = match.groups()
            is_birth_year = label.startswith("出生") or unit == "年" or len(number) == 4
            age = int(number)
            if is_birth_year or (unit is None and not 18 <= age <= 45):
                age = _birth_age(number)
            if age is not None and 18 <= age <= 45:
                return index, age
    for index, line in enumerate(folded_lines):
        line = line.strip(" []【】()。!、")
        if (match := _DIRECT_AGE.match(line)) and 18 <= (age := int(match[1])) <= 45:
            return index, age
        for pattern in _PROFILE_YEAR:
            match = pattern.match(line)
            if match and (age := _birth_age(match["year"])) is not None:
                return index, age
    # A standalone 05 is a year token. Inside a larger card, a bare
    # year on its own line needs an adjacent, explicitly marked height.
    for index, line in enumerate(folded_lines):
        if not re.fullmatch(r"\d{2}", line):
            continue
        neighbors = folded_lines[max(0, index - 1):index + 2]
        has_height = any(
            re.fullmatch(_HEIGHT_TOKEN, neighbor, re.I)
            and re.search(r"身高|净高|cm|厘米", neighbor, re.I)
            for neighbor in neighbors
        )
        if (len(folded_lines) == 1 or has_height) and (age := _birth_age(line)) is not None:
            return index, age
    return None


def normalize_weight(value) -> float | None:
    """Normalize a raw weight token using the OCR unit heuristic."""
    match = _WEIGHT_TOKEN.fullmatch(_fold_input(str(value)))
    if not match:
        return None
    number, unit = match.groups()
    weight = float(number)
    if 80 <= weight <= 160 or (38 <= weight < 75 and unit == "斤"):
        return round(weight / 2, 1)
    if 38 <= weight < 75:
        return round(weight, 1)
    return None


def find_weight(text: str) -> tuple[str, float, float] | None:
    """Return the observed token, its numeric value, and kilograms."""
    for match in _WEIGHT_LABEL.finditer(_fold_input(text)):
        weight = normalize_weight(match.group(0).removeprefix("体重").lstrip(" :="))
        if weight is not None:
            return match.group(0).strip(), float(match.group(1)), weight
    return None


def find_bust(text: str) -> str | None:
    folded = _snapshot().parser_aliases.replace(_fold_input(text))
    match = _BUST_BEFORE.search(folded) or _BUST_AFTER.search(folded) or _BUST_STANDALONE.fullmatch(folded)
    return (match[1] or match[2]).upper() if match else None


# Parse the amount before slang rewriting so compact money + duration stays split.
_TARIFF_LABELS = {
    "1p": r"(?:一\s*p|1\s*(?:p|次(?:价格)?)|一次(?:价格)?|单次(?:价格)?|p)",
    "2p": r"(?:二\s*p|2\s*(?:p|次(?:价格)?)|两次(?:价格)?|双次(?:价格)?|pp)",
    "bt": r"(?:bt|基础课时)",
}
_AMOUNT = rf"(?:\d{{1,5}}(?:\.\d+)?\s*{_SLANG_UNIT}|\d{{2,5}}(?:\.\d+)?)"
_DURATION = re.compile(r"(?<![\d.])(\d{1,3})\s*(?:分钟|分(?!钟)|min)(?![A-Za-z])", re.I)
_TARIFF_PREFIX = {
    key: re.compile(
        rf"(?<![0-9A-Za-z]){label}\s*(?:价格)?\s*[:=]?\s*[¥￥]?\s*(?P<amount>{_AMOUNT})"
        r"(?:\s*(?:元|块)|(?<![\d.])(?=\d{1,3}\s*(?:分钟|分|min))"
        r"|(?![\d.A-Za-z]|\s*(?:分钟|分|min|cm|厘米|kg|公斤|斤|岁|照片|图片|票|券|桌)))",
        re.I,
    )
    for key, label in _TARIFF_LABELS.items()
}
_TARIFF_SUFFIX = {
    key: re.compile(
        rf"(?<![0-9A-Za-z.])(?P<amount>\d{{3,5}}(?:\.\d+)?|\d{{1,5}}(?:\.\d+)?\s*{_SLANG_UNIT})"
        rf"\s*(?:元|块)?\s*/?\s*{marker}(?![0-9A-Za-z])",
        re.I,
    )
    for key, marker in {"1p": r"(?:p|次\b)", "2p": r"(?:pp|2\s*p)", "bt": r"bt"}.items()
}


def format_quote(amount: float, minutes=None) -> str:
    text = f"{int(round(amount))} 元"
    return f"{text} ({minutes}分钟)" if minutes else text


def tariff_quotes(lines) -> dict:
    """Return the first quote per tier with a duration from the same line."""
    quotes = {}
    for line in lines or []:
        folded = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", _apply_dynamic_terms(_fold_input(str(line))))
        # Prefer the full label over an overlapping bare P (e.g. 二P).
        candidates = [
            (hit.start(), tier, hit)
            for tier, pattern in _TARIFF_PREFIX.items()
            for hit in pattern.finditer(folded)
        ]
        prefixes = []
        for start, tier, hit in sorted(candidates, key=lambda candidate: (candidate[0], -candidate[2].end())):
            if not prefixes or start >= prefixes[-1][2].end():
                prefixes.append((start, tier, hit))
        suffixes = [
            (hit.start(), tier, hit)
            for tier, pattern in _TARIFF_SUFFIX.items()
            for hit in pattern.finditer(folded)
            if not any(start < hit.end() and hit.start() < prefix.end() for start, _, prefix in prefixes)
        ]
        hits = sorted(prefixes + suffixes)
        for index, (_, tier, hit) in enumerate(hits):
            if tier in quotes:
                continue
            amount = float(money_normalize(hit["amount"].replace(" ", "")))
            end = hits[index + 1][0] if index + 1 < len(hits) else len(folded)
            duration = _DURATION.search(folded, hit.end(), end)
            minutes = int(duration.group(1)) if duration else None
            quotes[tier] = {
                "amount": amount, "minutes": minutes,
                "text": format_quote(amount, minutes), "line": str(line),
            }
    return quotes


_EXTRA_FEE = re.compile(
    rf"(?P<label>(?:任意)?(?:[一二两三四五六七八九十]+|[1-9]\d?)\s*小时|包时|包夜)"
    rf"\s*[:=]?\s*[¥￥]?\s*(?P<amount>{_AMOUNT})(?![\d.A-Za-z])",
    re.I,
)
_RESTRICTION = re.compile(
    r"(?:禁(?!忌)|不(?:做|接(?:受)?|闲聊|催|亲|口嗨))[^\s,，。；;、|/！!？?：:]*"
)


def additional_fee_labels(line: str) -> list[str]:
    folded = _apply_dynamic_terms(_fold_input(line))
    return [
        f'{match["label"]} {format_quote(float(money_normalize(match["amount"])))}'
        for match in _EXTRA_FEE.finditer(folded)
    ]


def restriction_terms(line: str) -> list[str]:
    return keyword_tags(line).get("restriction_text", [])


# FlashText advances past the boundary character after a hit. Spacing the
# scan input preserves adjacent CJK/emoji keywords and offsets into source text.
_TRIE_SEPARATOR = "\0"
_ASCII_WORD_EDGE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_STATIC_ALIASES = {
    "一次": "1p", "单次": "1p", "1次": "1p", "一P": "1p", "一p": "1p",
    "两次": "2p", "双次": "2p", "2次": "2p", "二P": "2p", "二p": "2p",
    "大熊": "胸围", "熊": "胸围", "🐻": "胸围", "🐻️": "胸围",
    "通宵": "包夜",
}
_STATIC_TAGS = {
    "禁止拍照": ("restriction_text", "禁止拍照"),
    **{term: ("restriction_text", term) for term in (
        "禁嗑药", "禁无套", "禁醉酒", "禁桩基", "禁拔套", "禁鸽子", "禁变态",
        "不闲聊无口嗨", "不做无套", "不接受20岁以下", "不催赶", "不做口", "不亲吻",
    )},
    "包夜": ("service_text", "包夜"),
    "通宵": ("service_text", "包夜"),
    "包时": ("service_text", "包时"),
}
_SLANG_CATEGORY_MAP = {
    "money_slang": "money", "tariff_alias": "tariff", "bust_alias": "bust",
    "age_alias": "age", "restriction_alias": "restriction", "service_alias": "service",
}


def _processor(entries: dict) -> KeywordProcessor:
    processor = KeywordProcessor(case_sensitive=True)
    processor.set_non_word_boundaries(set())
    for term, value in entries.items():
        processor.add_keyword(_TRIE_SEPARATOR.join(term), value)
    return processor


def _spans(processor: KeywordProcessor, text: str):
    if processor:
        for value, start, end in processor.extract_keywords(_TRIE_SEPARATOR.join(text), span_info=True):
            yield value, start // 2, (end + 1) // 2


def _blocked_edge(text: str, start: int, end: int) -> bool:
    return (
        start > 0 and text[start] in _ASCII_WORD_EDGE and text[start - 1] in _ASCII_WORD_EDGE
    ) or (
        end < len(text) and text[end - 1] in _ASCII_WORD_EDGE and text[end] in _ASCII_WORD_EDGE
    )


@dataclass(frozen=True)
class _Aliases:
    trie: KeywordProcessor
    guarded: bool

    @classmethod
    def compile(cls, entries):
        guarded = any(term[0] in _ASCII_WORD_EDGE or term[-1] in _ASCII_WORD_EDGE
                      or _TRIE_SEPARATOR in target for term, target in entries.items())
        return cls(_processor(entries), guarded)

    def replace(self, text: str) -> str:
        if not self.trie:
            return text
        if not self.guarded and _TRIE_SEPARATOR not in text:
            return self.trie.replace_keywords(_TRIE_SEPARATOR.join(text)).replace(_TRIE_SEPARATOR, "")
        parts = []
        cursor = 0
        for target, start, end in _spans(self.trie, text):
            if not _blocked_edge(text, start, end):
                parts.extend((text[cursor:start], target))
                cursor = end
        parts.append(text[cursor:])
        return "".join(parts)


@dataclass(frozen=True)
class _TermSnapshot:
    entries: dict
    aliases: _Aliases
    parser_aliases: _Aliases
    money: KeywordProcessor
    dynamic_money: KeywordProcessor
    tags: KeywordProcessor
    tag_entries: dict


def _compile_terms(rows) -> _TermSnapshot:
    entries = {key: [] for key in _SLANG_CATEGORY_MAP.values()}
    aliases, money = {}, {}
    tags = dict(_STATIC_TAGS)
    for row in rows or ():
        category = _SLANG_CATEGORY_MAP.get(str(row.get("category") or ""))
        term = _fold_input(str(row.get("source_term") or ""))
        target = str(row.get("target_action") or "").strip()
        if not category or not term or len(term) > 64 or row.get("enabled") is False:
            continue
        factor = row.get("factor")
        if category == "money":
            try:
                factor = int(factor or 100)
            except (TypeError, ValueError):
                continue
            if not 1 <= factor <= 100000:
                continue
            money[term] = (term, factor, True)
        elif not target:
            continue
        elif category in {"restriction", "service"}:
            tags[term] = (f"{category}_text", target)
        else:
            aliases[term] = target
        entries[category].append({"term": term, "target": target, "factor": factor})
    return _TermSnapshot(
        entries, _Aliases.compile(aliases), _Aliases.compile(_STATIC_ALIASES | aliases),
        _processor({**_BASE_MONEY_ENTRIES, **money}), _processor(money), _processor(tags), tags,
    )


_BASE_MONEY_ENTRIES = {term: (term, factor, False) for term, factor in _STATIC_MONEY.items()}
_BASE_MONEY = _processor(_BASE_MONEY_ENTRIES)
_EMPTY_TERMS = _compile_terms(())
_ACTIVE_TERMS = _EMPTY_TERMS
_TERMS_LOCK = Lock()
_PREVIEW_TERMS: ContextVar[_TermSnapshot | None] = ContextVar("slang_preview", default=None)


def _snapshot() -> _TermSnapshot:
    return _PREVIEW_TERMS.get() or _ACTIVE_TERMS


def apply_dynamic_terms(rows) -> int:
    """Build complete replacement tries, then publish them in one swap."""
    global _ACTIVE_TERMS
    snapshot = _compile_terms(rows)
    with _TERMS_LOCK:
        _ACTIVE_TERMS = snapshot
    return sum(len(items) for items in snapshot.entries.values())


def clear_dynamic_terms() -> None:
    apply_dynamic_terms(())


def dynamic_terms() -> dict:
    return {key: [dict(item) for item in items] for key, items in _snapshot().entries.items()}


@contextmanager
def preview_dynamic_terms(rows):
    """Use a candidate dictionary in this task without publishing it."""
    token = _PREVIEW_TERMS.set(_compile_terms(rows))
    try:
        yield
    finally:
        _PREVIEW_TERMS.reset(token)


def _apply_dynamic_aliases(text: str) -> str:
    return _snapshot().aliases.replace(text)


def _apply_dynamic_terms(text: str) -> str:
    snapshot = _snapshot()
    return _rewrite_money_units(snapshot.parser_aliases.replace(text), snapshot.dynamic_money)


def keyword_tags(line: str, processor=None) -> dict[str, list[str]]:
    """Scan configured labels once, with the old free-form restriction fallback."""
    text = _fold_input(line)
    trie = _snapshot().tags if processor is None else processor
    hits = [
        (start, end, field, value)
        for (field, value), start, end in _spans(trie, text)
        if not _blocked_edge(text, start, end)
    ]
    hits.extend((match.start(), match.end(), "restriction_text", match[0])
                for match in _RESTRICTION.finditer(text) if len(match[0]) > 1)
    result: dict[str, list[str]] = {}
    previous_end = 0
    for start, end, field, value in sorted(hits, key=lambda hit: (hit[0], -hit[1])):
        if start >= previous_end:
            result.setdefault(field, []).append(value)
            previous_end = end
    return result


def _configured_tags(cfg: dict) -> KeywordProcessor:
    snapshot = _snapshot()
    tags = {}
    for field in ("restriction_text", "service_text"):
        terms = cfg.get(field)
        if isinstance(terms, list):
            terms = {term: term for term in terms if isinstance(term, str)}
        if not isinstance(terms, dict):
            continue
        for source, target in terms.items():
            if isinstance(source, str) and isinstance(target, str) and source.strip() and target.strip():
                tags[_fold_input(source)] = (field, target.strip())
    return _processor(snapshot.tag_entries | tags) if tags else snapshot.tags


# ---------------------------------------------------------------------------
# tolerant chunking
# ---------------------------------------------------------------------------

_BRACKET_EDGES = re.compile(
    r"^[\s（(【\[「『“\"']*(.*?)[\s）)】\]」』”\"'，,;；.!！?？]*$"
)
_LABEL_ONLY = re.compile(r"[^\s\d@:：]{1,12}[:：]?")
_VALUE_LINE = re.compile(r"^(?:\d|@|[tT]\.me)")


def _chunks(text: str) -> list:
    """The whole text first, then tolerant re-slices: bracket-peeled
    lines, the key and value halves of labeled lines, and label+value
    pairs whose parts ended up on separate lines. Whole-text matching
    keeps priority, so chunks only ever add hits."""
    seen: list = []

    def push(chunk):
        chunk = chunk.strip()
        if chunk and chunk not in seen:
            seen.append(chunk)

    push(text)
    peeled = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _BRACKET_EDGES.match(line)
        if match and match.group(1).strip():
            line = match.group(1).strip()
        peeled.append(line)
        push(line)
    for line in peeled:
        if ":" in line or "：" in line:
            key, value = re.split(r"[:：]", line, maxsplit=1)
            push(key)
            push(value)
    for index in range(len(peeled) - 1):
        # a label and its value that ended up on separate lines: offer
        # the common join forms so strict ("课费:800"), loose
        # ("课费 800", "课费: 800") and colon-less ("课费800")
        # patterns all match
        if _LABEL_ONLY.fullmatch(peeled[index]) and _VALUE_LINE.match(peeled[index + 1]):
            label = peeled[index].rstrip(":：")
            value = peeled[index + 1]
            push(f"{label} {value}")
            push(f"{label}:{value}")
            push(f"{label}: {value}")
            push(f"{label}{value}")
    return seen


def clean_username(value: str) -> str:
    """Usernames are always stored bare: drop "@", "t.me/" and URL
    wrappers no matter what the rule pattern captured."""
    value = (value or "").strip()
    value = re.sub(r"(?i)^(?:https?://)?(?:www\.)?(?:t(?:elegram)?\.me/)", "", value)
    return value.lstrip("@").strip()


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


def _match_value(found):
    if found and found.groups():
        value = found.group(1).strip()
        if value:
            return value
    elif found and found.group(0).strip():
        return found.group(0).strip()
    return None


def _first_match(patterns, text):
    chunks = None
    for pattern in patterns or []:
        value = _match_value(re.search(pattern, text))
        if value is not None:
            return value
        # whole-text miss: retry the same pattern against tolerant
        # chunks so brackets, line breaks and detached labels still hit
        if chunks is None:
            chunks = _chunks(text)
        for chunk in chunks[1:]:
            value = _match_value(re.search(pattern, chunk))
            if value is not None:
                return value
    return None


def _match_currency(currency_map: dict, text: str):
    for pattern, code in (currency_map or {}).items():
        if re.search(pattern, text):
            return code
    return None


_NUMBER_JUNK = re.compile(r"[^0-9.]")


def _to_amount(raw: str):
    """Best-effort numeric coercion for captured price text: currency
    marks and stray units around the number are dropped ("¥800元",
    "1,500" → 800 / 1500)."""
    cleaned = _NUMBER_JUNK.sub("", raw or "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_fields(rule: dict, text: str, src: str = "regex") -> tuple:
    """Return (fields, evidence) where evidence records the source of
    each extracted value: {"price": {"value": 888, "src": "regex"}}.
    `src` labels the provenance of this pass ("regex" or "ocr").

    The text is normalized first (money slang, full-width marks), so
    gate/lifecycle keep running on the raw message while extraction
    sees canonical forms."""
    cfg = (rule or {}).get("extract") or {}
    lines = text.splitlines() if text else []
    quotes = tariff_quotes(lines)
    text = normalize_input(text)
    fields = {}
    evidence = {}

    def put(name, value, line=None):
        fields[name] = value
        evidence[name] = {"value": value, "src": src}
        if line is not None:
            evidence[name]["line"] = line

    if age := find_age(lines):
        index, value = age
        put("age_years", value, lines[index])
    for line in lines:
        if "weight_kg" not in fields and (weight := find_weight(line)):
            put("weight_kg", weight[2], line)
        if "bust_size" not in fields and (bust := find_bust(line)):
            put("bust_size", bust, line)
    tags = _configured_tags(cfg)
    for line in lines:
        for key, values in keyword_tags(line, tags).items():
            entry = evidence.setdefault(key, {"value": [], "src": src, "line": line})
            entry["value"].extend(value for value in values if value not in entry["value"])
        if values := additional_fee_labels(line):
            entry = evidence.setdefault("additional_fee_text", {"value": [], "src": src, "line": line})
            entry["value"].extend(value for value in values if value not in entry["value"])
    for tier, quote in quotes.items():
        put(f"price_{tier}", quote["amount"], quote["line"])
        put(f"price_{tier}_display", quote["text"], quote["line"])

    for name in FIELDS:
        value = _first_match(cfg.get(name), text)
        if value:
            if name in {"nickname", "district"}:
                value = value.strip().lstrip("#").strip()
            # usernames are always stored bare ("abaoggio"); a pattern
            # that captures the "@" must not leak into the stored value
            if name == "username":
                value = clean_username(value)
            fields[name] = value
            evidence[name] = {"value": value, "src": src}

    # A price label inside a tier (e.g. 单次价格) must not capture the
    # unscaled prefix of a compact quote. Independent configured fees still win.
    price_text = re.sub(r"((?:[12]\s*次|一次|单次|两次|双次))价格", r"\1", text)
    price_raw = _first_match(cfg.get("price"), price_text)
    if price_raw is not None:
        amount = _to_amount(price_raw)
        if amount is not None:
            # optional per-rule scaling: "price_multiply": 100 turns a
            # unit quote like 6P into yuan (600)
            multiplier = cfg.get("price_multiply")
            if isinstance(multiplier, (int, float)) and multiplier:
                amount = amount * multiplier
            fields["price_amount"] = amount
            evidence["price_amount"] = {"value": amount, "src": src}

    if "price_amount" not in fields and quotes:
        quote = quotes.get("1p") or quotes.get("2p") or quotes["bt"]
        put("price_amount", quote["amount"], quote["line"])

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
