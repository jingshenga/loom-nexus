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
_BUST_PREFIX = re.compile(r"(?:胸围|罩杯|胸)[\s:：]?$")
# "8张" / "6章" → 800-scale money. 章/张 carry a digit guard per spec
# ("6章40分钟" is left untouched — folding would splice 600+40); photo
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
        prefix = match.string[max(0, match.start() - 6):match.start()]
        if _BUST_PREFIX.search(prefix):
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


def normalize_input(text: str) -> str:
    """Pre-extraction cleanup: NFKC full-width folding, keycap digit
    emojis back to digits, pointing marks to spaces, prohibition marks
    to 禁, dynamic slang-registry aliasing and money folding (hot
    reloadable, beats built-ins), zero-width and NBSP removal,
    digit-group commas and money-slang rewriting. Line structure is
    preserved — chunked matching relies on it."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _ZERO_WIDTH.sub("", text)
    text = _KEYCAP_DIGIT.sub(r"\1", text)
    text = _POINTING_MARKS.sub(" ", text)
    text = _PROHIBITION_MARKS.sub("禁", text)
    text = text.replace("\u00a0", " ")
    text = _apply_dynamic_terms(text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = _rewrite_money_units(text)
    return re.sub(r"[ \t\u3000]+", " ", text).strip()


# ---------------------------------------------------------------------------
# dynamic slang registry (hot-reloadable override layer)
#
# Built-in slang above stays hardcoded as the fallback; operators add
# new units/aliases (张/米/章 successors, "一次"→1p, 🐻→胸围, …) into the
# dynamic_slang_terms table and hot-apply them without a release. The
# registry is an in-memory snapshot applied inside normalize_input;
# alias terms fold BEFORE money units so custom entries always win.
# ---------------------------------------------------------------------------

_DYNAMIC_TERMS: dict = {"money": [], "tariff": [], "bust": [], "age": []}
_SLANG_CATEGORY_MAP = {
    "money_slang": "money", "tariff_alias": "tariff",
    "bust_alias": "bust", "age_alias": "age",
}


def apply_dynamic_terms(rows) -> int:
    """Replace the in-memory snapshot from DB rows shaped
    {"category", "source_term", "target_action", "factor"}; returns the
    number of usable entries. Invalid rows are skipped, never fatal."""
    bucket: dict = {key: [] for key in _SLANG_CATEGORY_MAP.values()}
    for row in rows or []:
        category = _SLANG_CATEGORY_MAP.get(str(row.get("category") or ""))
        term = str(row.get("source_term") or "").strip()
        if not category or not term or len(term) > 64:
            continue
        bucket[category].append({
            "term": term,
            "target": str(row.get("target_action") or "").strip(),
            "factor": row.get("factor"),
        })
    _DYNAMIC_TERMS.update(**bucket)
    return sum(len(items) for items in bucket.values())


def clear_dynamic_terms() -> None:
    _DYNAMIC_TERMS.update(money=[], tariff=[], bust=[], age=[])


def dynamic_terms() -> dict:
    return {key: list(items) for key, items in _DYNAMIC_TERMS.items()}


def _alias_replace(text: str, entries) -> str:
    """term → target_action. Latin-edged terms get word guards so
    "cup" never splices inside "600cup"; CJK/emoji-edged terms don't
    ("一次500" and "🐻C" must fold)."""
    for entry in entries:
        target = entry["target"]
        term = entry["term"]
        if not target:
            continue
        head = "(?<![0-9A-Za-z])" if re.match(r"[0-9A-Za-z]", term[:1]) else ""
        tail = "(?![0-9A-Za-z])" if re.match(r"[0-9A-Za-z]", term[-1:]) else ""
        text = re.sub(head + re.escape(term) + tail, target, text)
    return text


def _dynamic_money_re():
    """(compiled_pattern, {term: factor}) for the money_slang entries,
    or None when the registry is empty."""
    entries = _DYNAMIC_TERMS["money"]
    if not entries:
        return None
    factors = {}
    arms = []
    for entry in entries:
        try:
            factor = int(entry["factor"] or 100)
        except (TypeError, ValueError):
            factor = 100
        factors[entry["term"]] = factor
        arms.append(re.escape(entry["term"]))
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(" + "|".join(arms) + r")(?![0-9])")
    return pattern, factors


def _apply_dynamic_terms(text: str) -> str:
    if not any(_DYNAMIC_TERMS.values()):
        return text
    for group in ("tariff", "bust", "age"):
        if _DYNAMIC_TERMS[group]:
            text = _alias_replace(text, _DYNAMIC_TERMS[group])
    money = _dynamic_money_re()
    if money:
        pattern, factors = money
        text = pattern.sub(
            lambda match: _scaled(match.group(1), factors[match.group(2)]),
            text,
        )
    return text


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
    text = normalize_input(text)
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
                value = clean_username(value)
            fields[name] = value
            evidence[name] = {"value": value, "src": src}

    price_raw = _first_match(cfg.get("price"), text)
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
