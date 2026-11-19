"""Correction diff tracker: field-level manual fixes → feedback rows.

Every moderation-workbench save/approve/reject that changes extracted
values produces one listing_correction_diffs row per changed field
(system-extracted old vs operator-corrected new). The rules workshop
aggregates those diffs into slang suggestions, closing the loop:
wrong extraction → human fix → recorded diff → hot slang entry.

compute_diffs is pure; record() persists through the
correction_diffs_record() database function — the whole diff batch
rides as ONE jsonb parameter, so no SQL is ever assembled in Python.
"""
import json

from app import sqlres

SQL_RECORD = sqlres.load("correction_diff_insert.sql")

# keep in sync with moderation.AttributeEdits / review.WORKBENCH_ATTRIBUTE_KEYS
ATTRIBUTE_DIFF_FIELDS = (
    "age_years", "height_cm", "weight_kg", "bust_size",
    "price_1p_text", "price_2p_text", "service_text",
    "features_text", "restriction_text", "fixed_intro_terms",
)
BASE_DIFF_FIELDS = ("nickname", "district", "price_amount")


def _normalize_value(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, (list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def compute_diffs(before_custom, after_custom, base_before=None,
                  base_after=None, evidence=None) -> list:
    """Field-level diffs between the system-extracted values and the
    operator-corrected ones. Absent→set diffs read old="null"
    (e.g. age_years null → 19). Equal or both-absent fields produce
    nothing; never raises."""
    diffs = []
    for field in ATTRIBUTE_DIFF_FIELDS + BASE_DIFF_FIELDS:
        source_before, source_after = (
            (before_custom, after_custom) if field in ATTRIBUTE_DIFF_FIELDS
            else (base_before, base_after)
        )
        old = (source_before or {}).get(field)
        new = (source_after or {}).get(field)
        if old == new:
            continue
        entry = (evidence or {}).get(field)
        snippet = entry.get("line") if isinstance(entry, dict) else None
        diffs.append({
            "field_name": field,
            "old_value": _normalize_value(old),
            "new_value": _normalize_value(new),
            "raw_snippet": str(snippet)[:300] if snippet else None,
        })
    return diffs


async def record(pool, listing_id: int, diffs: list, operator: int) -> int:
    """Persist the diffs through correction_diffs_record(): the whole
    batch rides as ONE jsonb parameter (nothing is assembled in
    Python); returns rows written."""
    payload = json.dumps(diffs, ensure_ascii=False, default=str)
    count = await pool.fetchval(SQL_RECORD, listing_id, payload, operator)
    return int(count or 0)
