"""Admin LLM settings (stored in app_settings, effective immediately).

The API key is write-only: PUT accepts it, GET never returns it.
Also carries the auto-publish policy used by the extractor.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, llm, sqlres
from app.api.auth import require_admin

router = APIRouter(
    prefix="/api/admin/settings", tags=["admin-settings"],
    dependencies=[Depends(require_admin)],
)


class LlmBody(BaseModel):
    base_url: str = ""
    api_key: str = None          # blank/None keeps the stored key
    model: str = ""
    allow_private: bool = False
    autopublish_enabled: bool = True
    autopublish_threshold: float = 0.8


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


@router.get("/llm")
async def get_llm() -> dict:
    pool = _pool()
    cfg = await llm.load_config(pool)
    policy = await llm.load_auto_publish(pool)
    return {
        "configured": bool(cfg["base_url"] and cfg["api_key"]),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "allow_private": cfg["allow_private"],
        "has_key": bool(cfg["api_key"]),
        "source": cfg["source"],
        "autopublish_enabled": policy["enabled"],
        "autopublish_threshold": policy["threshold"],
    }


@router.put("/llm")
async def put_llm(body: LlmBody) -> dict:
    pool = _pool()
    await llm.save_config(
        pool, body.base_url, body.api_key, body.model, body.allow_private,
    )
    await pool.fetch(
        sqlres.load("settings_upsert.sql"),
        "autopublish_enabled", "1" if body.autopublish_enabled else "0",
    )
    await pool.fetch(
        sqlres.load("settings_upsert.sql"),
        "autopublish_threshold",
        str(max(0.0, min(1.0, body.autopublish_threshold))),
    )
    return {"ok": True, "note": "已保存，立即生效（key 仅保存不回显）"}


@router.post("/llm/test")
async def test_llm() -> dict:
    pool = _pool()
    cfg = await llm.load_config(pool)
    if not (cfg["base_url"] and cfg["api_key"] and cfg["model"]):
        raise HTTPException(status_code=400, detail="请先填写 base_url / api_key / 模型名")
    try:
        reply = llm.chat(cfg, [{"role": "user", "content": "回复：OK"}], timeout=30)
    except HTTPException as exc:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"调用失败: {exc}")
    return {"ok": True, "reply": (reply or "").strip()[:40]}
