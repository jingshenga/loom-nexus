"""Dynamic slang registry loader with a TTL snapshot.

The enabled rows of dynamic_slang_terms are pushed into
ast_rules.apply_dynamic_terms; the extractor loop refreshes the
snapshot on a TTL so new slang takes effect within a minute of the
admin saving it, and the rules API forces an immediate refresh after
every CRUD write — no service restart, no code change.
"""
import time

from app import sqlres
from app.extractor import ast_rules

SQL_ENABLED = sqlres.load("slang_terms_enabled.sql")

_TTL_S = 60.0
_last_refresh = {"at": 0.0, "count": 0}


async def ensure_fresh(pool, ttl_s: float = _TTL_S) -> int:
    """Reload the registry from the database when the snapshot is
    stale; returns the number of applied entries. Errors degrade to
    "keep the current snapshot" — extraction must never stall on it."""
    now = time.monotonic()
    if now - _last_refresh["at"] < ttl_s:
        return _last_refresh["count"]
    try:
        rows = await pool.fetch(SQL_ENABLED)
        count = ast_rules.apply_dynamic_terms(
            [dict(row) for row in rows],
        )
        _last_refresh.update(at=now, count=count)
    except Exception:
        _last_refresh["at"] = now
    return _last_refresh["count"]


async def reload(pool) -> int:
    """Force a refresh (after admin CRUD); returns applied count."""
    _last_refresh["at"] = 0.0
    return await ensure_fresh(pool, ttl_s=0.0)


def invalidate() -> None:
    """Mark the snapshot stale without touching it (next caller reloads)."""
    _last_refresh["at"] = 0.0


def mark_fresh(count: int) -> None:
    """Record that the caller just applied a snapshot itself (e.g. the
    rules API right after a CRUD write) so the TTL reload stays quiet."""
    _last_refresh.update(at=time.monotonic(), count=count)
