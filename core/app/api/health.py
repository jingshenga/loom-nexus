"""Health endpoint for nexus-core."""
import time

from fastapi import APIRouter

from app import db

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict:
    payload: dict = {
        "service": "nexus-core",
        "status": "ok",
        "db": "down",
        "db_latency_ms": None,
    }
    pool = db.get_pool()
    if pool is None:
        payload["status"] = "degraded"
        return payload
    started = time.perf_counter()
    try:
        await pool.fetchval("SELECT 1")
    except Exception:
        payload["status"] = "degraded"
        return payload
    payload["db"] = "up"
    payload["db_latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return payload
