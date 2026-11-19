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

# 0.8k / 8千 → 8000-scale rewrites; negative lookaheads keep "8cm",
# "2P" handles and other letter-suffixed tokens intact.
_MONEY_K = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[kK千])(?![0-9A-Za-z])")
_MONEY_W = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[wW万])(?![0-9A-Za-z])")
# "8c" → 800, but bra sizes ("胸围88C") stay untouched
_MONEY_C = re.compile(r"(\d+(?:\.\d+)?)\s*[cC](?![0-9A-Za-z])")
_BUST_LABEL = r"(?:胸围?|罩杯|(?<![A-Za-z])cup|大?熊|🐻\ufe0f?)"
_BUST_MODIFIERS = r"(?:(?:天然|自然|原装|真)\s*)?"
_BUST_PREFIX = re.compile(rf"{_BUST_LABEL}\s*[:=]?\s*{_BUST_MODIFIERS}$", re.I)
# "8张" / "6章" → 800-scale money. The digit guard prevents accidental
# concatenation. A recognized compact duration is separated first; photo
# counters ("3张照片") are excluded statically. "8米" folds EXCEPT when
# a digit follows ("1米68" height) or a height prefix precedes.
_MONEY_ZH = re.compile(r"(\d+(?:\.\d+)?)\s*([张章])(?![0-9])(?![照片图票券卡桌])")
_MONEY_MI = re.compile(r"(\d+(?:\.\d+)?)\s*米(?![照片图票券卡桌])")
_HEIGHT_PREFIX = re.compile(r"(?:身高|净高|身量|个头)\s*[:：]?\s*$")


def _scaled(number: str, factor: int) -> str:
    value = float(number) * factor
    return str(int(value)) if value == int(value) else str(value)


def _rewrite_money_units(text: str) -> str:
    """Colloquial money units become plain yuan numbers so brittle
    numeric rule patterns see "800" whether the message said 0.8k,
    8张, 8米, 6章 or 8c."""

    def k_repl(match):
        return _scaled(match.group(1), 1000)

    def w_repl(match):
        return _scaled(match.group(1), 10000)

    def c_repl(match):
        prefix = match.string[max(0, match.start() - 24):match.start()]
        if _BUST_PREFIX.search(prefix) or match.string[match.end():].lstrip().startswith("罩杯"):
            return match.group(0)
        return _scaled(match.group(1), 100)

    def zh_repl(match):
        return _scaled(match.group(1), 100)

    def mi_repl(match):
        unit_tail = match.string[match.end():match.end() + 1]
        if unit_tail and (unit_tail.isdigit() or unit_tail == "."):
            return match.group(0)  # "1米68" / "1.5米…" — height, not money
        prefix = match.string[max(0, match.start() - 8):match.start()]
        if _HEIGHT_PREFIX.search(prefix):
            return match.group(0)
        return _scaled(match.group(1), 100)

    text = _MONEY_K.sub(k_repl, text)
    text = _MONEY_W.sub(w_repl, text)
    text = _MONEY_C.sub(c_repl, text)
    text = _MONEY_ZH.sub(zh_repl, text)
    return _MONEY_MI.sub(mi_repl, text)


# public alias: OCR card lines and tariff parsing need the same slang
# folding before they look for 1P/2P amounts ("P 6米" → "P 600")
money_normalize = _rewrite_money_units


def _fold_input(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _ZERO_WIDTH.sub("", text)
    text = _KEYCAP_DIGIT.sub(r"\1", text)
    text = _POINTING_MARKS.sub(" ", text)
    text = _PROHIBITION_MARKS.sub("禁", text)
    return re.sub(r"[ \t]+", " ", text).strip()


_SLANG_UNIT = r"[章张米kKcC千wW万]"
_COMPACT_MONEY_DURATION = re.compile(rf"(?<=\d)({_SLANG_UNIT})(?=\d{{1,3}}\s*(?:分钟|min\b))", re.I)


def normalize_input(text: str) -> str:
    """Fold width, emoji and money slang without changing line boundaries."""
    folded = re.sub(r"(?<=\d),(?=\d)", "", _fold_input(text))
    return _rewrite_money_units(_COMPACT_MONEY_DURATION.sub(r"\1 ", folded))


# Body fields are shared by text rules and OCR, which keeps the original line as evidence.
_AGE_LABEL = re.compile(r"(年龄|年纪|出生(?:年份)?)\s*[:=]?\s*(\d{4}|\d{1,2})(?![\d.])\s*([岁年])?(?![岁年\dA-Za-z.])")
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
_CUP = r"(?:\d{2}\s*)?([A-I]\+?)"
_BUST_BEFORE = re.compile(rf"{_BUST_LABEL}\s*[:=]?\s*{_BUST_MODIFIERS}{_CUP}(?![A-Za-z0-9+])", re.I)
_BUST_AFTER = re.compile(rf"(?<![A-Za-z0-9]){_CUP}\s*罩杯", re.I)


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
    folded_lines = [_fold_input(line) for line in lines]
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
        for pattern in _PROFILE_YEAR:
            match = pattern.match(line.strip(" []【】()。!、"))
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
    folded = _fold_input(text)
    match = _BUST_BEFORE.search(folded) or _BUST_AFTER.search(folded)
    return match.group(1).upper() if match else None


# Parse the amount before slang rewriting so compact money + duration stays split.
_TARIFF_LABELS = {
    "1p": r"(?:1\s*(?:p|次(?:价格)?)|一次(?:价格)?|单次(?:价格)?|p)",
    "2p": r"(?:2\s*(?:p|次(?:价格)?)|两次(?:价格)?|双次(?:价格)?|pp)",
}
_AMOUNT = rf"(?:\d{{1,5}}(?:\.\d+)?\s*{_SLANG_UNIT}|\d{{2,5}}(?:\.\d+)?)"
_DURATION = re.compile(r"(?<![\d.])(\d{1,3})\s*(?:分钟|分(?!钟)|min)(?![A-Za-z])", re.I)
_TARIFF_PREFIX = {
    key: re.compile(
        rf"(?<![0-9A-Za-z]){label}\s*[:=]?\s*[¥￥]?\s*(?P<amount>{_AMOUNT})"
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
    for key, marker in {"1p": r"(?:p|次\b)", "2p": r"(?:pp|2\s*p)"}.items()
}


def format_quote(amount: float, minutes=None) -> str:
    text = f"{int(round(amount))} 元"
    return f"{text} ({minutes}分钟)" if minutes else text


def tariff_quotes(lines) -> dict:
    """Return the first quote per tier with a duration from the same line."""
    quotes = {}
    for line in lines or []:
        folded = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", _fold_input(str(line)))
        prefixes = [
            (hit.start(), tier, hit)
            for tier, pattern in _TARIFF_PREFIX.items()
            for hit in pattern.finditer(folded)
        ]
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
        quote = quotes.get("1p") or quotes["2p"]
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
