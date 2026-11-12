"""Admin controls for OCR media labels, classifier training, and release."""
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import db
from app.api.auth import current_user_id, require_admin
from app.config import get_settings
from app.extractor.image_classifier import (
    as_card_binary_samples, predict, split_samples, train_and_validate,
)

router = APIRouter(
    prefix="/api/admin/ocr-classifier", tags=["admin-ocr-classifier"],
    dependencies=[Depends(require_admin)],
)


class CategoryBody(BaseModel):
    code: str
    name: str
    review_required: bool = True


class CategoryUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    review_required: bool | None = None


class TrainBody(BaseModel):
    classification_mode: Literal["multi_class", "card_binary"] = "multi_class"
    confidence_threshold: float = Field(default=0.65, ge=0, le=1)
    review_classes: list[str] = Field(default_factory=lambda: ["info_card", "uncertain"])
    min_samples_per_class: int = Field(default=5, ge=2, le=1000)
    min_macro_f1: float = Field(default=0.7, ge=0, le=1)


class ReclassifyBody(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


def _json(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _version(row) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "parent_version_id": row["parent_version_id"],
        "policy": _json(row["policy_json"], {}),
        "metrics": _json(row["metrics_json"], {}),
        "shadow_count": row["shadow_count"] if "shadow_count" in row else 0,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "prepared_at": row["prepared_at"].isoformat() if row["prepared_at"] else None,
        "published_at": row["published_at"].isoformat() if row["published_at"] else None,
    }


@router.get("/categories")
async def list_categories() -> dict:
    rows = await _pool().fetch(
        "SELECT c.code, c.name, c.is_active, c.is_trainable, c.review_required, c.is_builtin, "
        "(SELECT count(*) FROM ocr_classification_labels l "
        " WHERE l.category_code=c.code AND l.superseded_at IS NULL) AS labeled_count, "
        "(SELECT count(*) FROM ocr_classification_labels l "
        " WHERE l.category_code=c.code AND l.superseded_at IS NULL AND l.label_source='manual') AS manual_label_count, "
        "(SELECT count(*) FROM ocr_classification_labels l "
        " WHERE l.category_code=c.code AND l.superseded_at IS NULL AND l.label_source='legacy_binary') AS legacy_label_count "
        "FROM ocr_classification_categories c ORDER BY c.is_builtin DESC, c.created_at, c.code"
    )
    return {"items": [dict(row) for row in rows]}


@router.post("/categories")
async def create_category(body: CategoryBody, authorization: str = Header(default="")) -> dict:
    code = body.code.strip().lower().replace("-", "_")
    name = body.name.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,47}", code) or not name:
        raise HTTPException(status_code=422, detail="类别代码需为 2–48 位英文、数字或下划线，名称不能为空")
    try:
        row = await _pool().fetchrow(
            "INSERT INTO ocr_classification_categories (code, name, review_required, created_by) "
            "VALUES ($1,$2,$3,$4) RETURNING code,name,is_active,is_trainable,review_required,is_builtin",
            code, name, body.review_required, current_user_id(authorization),
        )
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="类别代码或名称已存在") from exc
        raise
    return dict(row)


@router.patch("/categories/{code}")
async def update_category(code: str, body: CategoryUpdate) -> dict:
    pool = _pool()
    row = await pool.fetchrow(
        "UPDATE ocr_classification_categories SET "
        "name=COALESCE($2,name), is_active=COALESCE($3,is_active), "
        "review_required=COALESCE($4,review_required), updated_at=NOW() "
        "WHERE code=$1 RETURNING code,name,is_active,is_trainable,review_required,is_builtin",
        code, body.name.strip() if body.name is not None else None,
        body.is_active, body.review_required,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="类别不存在")
    return dict(row)


@router.get("/versions")
async def list_versions() -> dict:
    rows = await _pool().fetch(
        "SELECT v.id,v.status,v.parent_version_id,v.policy_json,v.metrics_json,v.created_at,v.prepared_at,v.published_at, "
        "(SELECT count(*) FROM ocr_classifier_shadow_predictions p WHERE p.version_id=v.id) AS shadow_count "
        "FROM ocr_classifier_versions v ORDER BY v.id DESC LIMIT 50"
    )
    return {"items": [_version(row) for row in rows]}


async def _selected_engine(pool) -> str:
    value = await pool.fetchval("SELECT value FROM app_settings WHERE key='ocr_classifier_engine'")
    return value if value in ("vision4", "legacy") else (
        "vision4" if get_settings().vision4_mode == "active" else "legacy"
    )


@router.get("/engine")
async def classifier_engine() -> dict:
    return {"selected_engine": await _selected_engine(_pool())}


@router.get("/vision4")
async def vision4_status(request: Request) -> dict:
    pool = _pool()
    settings = get_settings()
    try:
        manifest = json.loads((Path(settings.vision4_model_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    sha = manifest.get("model_sha256")
    counts = await pool.fetch(
        "SELECT status,count(*) AS n FROM media_vision_asset_predictions WHERE model_sha=$1 GROUP BY status", sha,
    ) if sha else []
    rows = await pool.fetch(
        "SELECT p.mapped_classification AS predicted,l.category_code AS actual,count(*) AS n "
        "FROM media_vision_predictions p JOIN LATERAL ("
        " SELECT category_code FROM ocr_classification_labels l WHERE l.ocr_id=p.ocr_id "
        " AND l.superseded_at IS NULL AND l.label_source='manual' ORDER BY l.created_at DESC,l.id DESC LIMIT 1"
        ") l ON TRUE WHERE p.model_sha=$1 AND p.status='classified' "
        "AND l.category_code=ANY($2::text[]) GROUP BY p.mapped_classification,l.category_code",
        sha, ["info_card", "profile_photo", "sixiang", "other"],
    ) if sha else []
    total = sum(row["n"] for row in rows)
    matched = sum(row["n"] for row in rows if row["actual"] == row["predicted"])
    processor = getattr(request.app.state, "media_ocr_processor", None)
    model = processor._vision4 if processor else None
    return {
        "selected_engine": await _selected_engine(pool), "mode": settings.vision4_mode,
        "model_version": manifest.get("model_version"), "model_sha": sha,
        "package_status": manifest.get("status"),
        "runtime_status": "loaded" if model and model._session is not None else
                          "error" if model and model._load_error else "not_loaded",
        "runtime_error": model._load_error if model else None,
        "counts": {row["status"]: row["n"] for row in counts},
        "manual_comparison": {"total": total, "matched": matched,
                              "agreement": matched / total if total else None,
                              "rows": [dict(row) for row in rows]},
        "validation_accuracy": None,
    }


@router.post("/vision4/activate")
async def activate_vision4(request: Request) -> dict:
    if get_settings().vision4_mode != "active":
        raise HTTPException(status_code=409, detail="V4 推理未启用，请检查服务配置")
    processor = getattr(request.app.state, "media_ocr_processor", None)
    if processor is None:
        raise HTTPException(status_code=503, detail="OCR 处理器尚未就绪")
    try:
        import asyncio
        await asyncio.to_thread(processor._vision4._ensure_loaded)
    except Exception:
        raise HTTPException(status_code=409, detail="V4 模型加载或校验失败")
    await _pool().execute(
        "INSERT INTO app_settings(key,value,updated_at) VALUES('ocr_classifier_engine','vision4',NOW()) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()"
    )
    return {"ok": True, "selected_engine": "vision4"}


@router.post("/vision4/reclassify")
async def reclassify_vision4(body: ReclassifyBody, request: Request) -> dict:
    import asyncio

    if not body.ids:
        raise HTTPException(status_code=422, detail="请选择要重新分类的图片")
    pool = _pool()
    if await _selected_engine(pool) != "vision4" or get_settings().vision4_mode != "active":
        raise HTTPException(status_code=409, detail="当前没有启用 V4")
    processor = getattr(request.app.state, "media_ocr_processor", None)
    if processor is None:
        raise HTTPException(status_code=503, detail="OCR 处理器尚未就绪")
    rows = await pool.fetch("SELECT id,media_path FROM listing_media_ocr WHERE id=ANY($1::bigint[])", body.ids)
    updated = failed = 0
    for row in rows:
        path = (processor._media_dir / row["media_path"]).resolve()
        if not path.is_relative_to(processor._media_dir) or not path.is_file():
            failed += 1
            continue
        async with processor._vision4_gate:
            result = await asyncio.to_thread(processor._vision4.predict, str(path))
        saved = await processor._run_vision4(row, path, result)
        if result.get("status") == "classified" and saved:
            updated += 1
        else:
            failed += 1
    return {"updated": updated, "failed": failed, "human_labels_preserved": True}


@router.post("/train")
async def train(body: TrainBody, authorization: str = Header(default="")) -> dict:
    pool = _pool()
    categories = await pool.fetch(
        "SELECT code,review_required FROM ocr_classification_categories "
        "WHERE is_active AND is_trainable ORDER BY code"
    )
    source_classes = [row["code"] for row in categories]
    counts_rows = await pool.fetch(
        "SELECT category_code, count(*) AS n FROM ocr_classification_labels "
        "WHERE superseded_at IS NULL AND category_code = ANY($1::text[]) GROUP BY category_code",
        source_classes,
    )
    source_counts = {row["category_code"]: row["n"] for row in counts_rows}
    if body.classification_mode == "card_binary":
        train_classes = ["info_card", "other"]
        counts = {
            "info_card": source_counts.get("info_card", 0),
            "other": sum(source_counts.get(code, 0) for code in source_classes if code != "info_card"),
        }
    else:
        counts = {code: source_counts.get(code, 0) for code in source_classes}
        train_classes = [code for code in source_classes if counts[code] >= 2]
    if len(train_classes) < 2 or any(counts.get(code, 0) < 2 for code in train_classes):
        message = (
            "资料卡和非资料卡各至少需要 2 张有效标注图片"
            if body.classification_mode == "card_binary"
            else "至少两个启用类别各需要 2 张有效标注图片"
        )
        raise HTTPException(
            status_code=409,
            detail={"message": message, "counts": counts},
        )
    samples_rows = await pool.fetch(
        "SELECT DISTINCT ON (label.ocr_id) label.ocr_id AS id,label.category_code AS label, "
        "COALESCE(NULLIF(o.normalized_text,''),o.raw_text,'') AS text, "
        "o.classifier_signals_json AS signals, s.id::text AS source_group, "
        "COALESCE('raw:'||o.raw_id::text,'listing:'||o.listing_id::text,'ocr:'||o.id::text) AS message_group "
        "FROM ocr_classification_labels label JOIN listing_media_ocr o ON o.id=label.ocr_id "
        "LEFT JOIN raw_messages rm ON rm.id=o.raw_id LEFT JOIN ingest_sources s ON s.id=rm.source_id "
        "WHERE label.superseded_at IS NULL AND label.category_code = ANY($1::text[]) "
        "ORDER BY label.ocr_id,label.created_at DESC,label.id DESC",
        source_classes,
    )
    samples = [{
        "id": row["id"], "label": row["label"], "text": row["text"],
        "signals": _json(row["signals"], {}), "source_group": row["source_group"],
        "message_group": row["message_group"],
    } for row in samples_rows]
    if body.classification_mode == "card_binary":
        samples = as_card_binary_samples(samples)
    try:
        model, metrics = train_and_validate(samples, train_classes)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Keep the holdout predictions so policy thresholds can be simulated before release.
    validation_cases = []
    _, validation_rows, _ = split_samples(samples, train_classes)
    for row in validation_rows:
        predicted, confidence, _ = predict(model, row["text"], row["signals"])
        validation_cases.append({
            "actual": row["label"], "predicted": predicted, "confidence": round(confidence, 5),
        })
    metrics["validation_cases"] = validation_cases
    metrics["excluded_categories"] = {} if body.classification_mode == "card_binary" else {
        row["code"]: counts.get(row["code"], 0)
        for row in categories if row["code"] not in train_classes
    }
    policy = {
        "classification_mode": body.classification_mode,
        "confidence_threshold": body.confidence_threshold,
        "review_classes": (
            ["info_card"] if body.classification_mode == "card_binary"
            else [row["code"] for row in categories if row["review_required"]]
        ),
        "min_samples_per_class": body.min_samples_per_class,
        "min_macro_f1": body.min_macro_f1,
    }
    active = await pool.fetchrow(
        "SELECT id FROM ocr_classifier_versions WHERE status='active' ORDER BY id DESC LIMIT 1"
    )
    row = await pool.fetchrow(
        "INSERT INTO ocr_classifier_versions (status,parent_version_id,model_json,policy_json,metrics_json,created_by) "
        "VALUES ('tested',$1,$2::jsonb,$3::jsonb,$4::jsonb,$5) "
        "RETURNING id,status,parent_version_id,policy_json,metrics_json,created_at,prepared_at,published_at",
        active["id"] if active else None, json.dumps(model), json.dumps(policy),
        json.dumps(metrics), current_user_id(authorization),
    )
    return _version(row)


@router.post("/versions/{version_id}/prepare")
async def prepare_version(version_id: int) -> dict:
    pool = _pool()
    row = await pool.fetchrow(
        "SELECT id,status,policy_json,metrics_json FROM ocr_classifier_versions WHERE id=$1",
        version_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    metrics = _json(row["metrics_json"], {})
    policy = _json(row["policy_json"], {})
    shadow_count = await pool.fetchval(
        "SELECT count(*) FROM ocr_classifier_shadow_predictions WHERE version_id=$1",
        version_id,
    )
    if shadow_count < 1:
        raise HTTPException(status_code=409, detail="请先运行影子测试；若没有可测图片，请先补充待分类图片")
    counts = metrics.get("class_counts", {})
    counts.update(metrics.get("excluded_categories", {}))
    minimum = int(policy.get("min_samples_per_class", 5))
    weak = {name: count for name, count in counts.items() if count < minimum}
    score = float(metrics.get("macro_f1", 0))
    target = float(policy.get("min_macro_f1", 0.7))
    if weak or score < target:
        raise HTTPException(status_code=409, detail={
            "message": "尚未达到准备上线的验证要求",
            "under_sampled_classes": weak,
            "macro_f1": score,
            "required_macro_f1": target,
        })
    row = await pool.fetchrow(
        "UPDATE ocr_classifier_versions SET status='ready',prepared_at=NOW() "
        "WHERE id=$1 AND status='tested' "
        "RETURNING id,status,parent_version_id,policy_json,metrics_json,created_at,prepared_at,published_at",
        version_id,
    )
    if row is None:
        raise HTTPException(status_code=409, detail="只有完成验证的测试版本可以准备上线")
    return _version(row)


@router.post("/versions/{version_id}/publish")
async def publish_version(version_id: int) -> dict:
    pool = _pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            candidate = await conn.fetchrow(
                "SELECT id,status FROM ocr_classifier_versions WHERE id=$1 FOR UPDATE",
                version_id,
            )
            if candidate is None:
                raise HTTPException(status_code=404, detail="版本不存在")
            if candidate["status"] not in {"ready", "retired", "active"}:
                raise HTTPException(status_code=409, detail="请先完成测试并点击准备上线")
            await conn.execute(
                "UPDATE ocr_classifier_versions SET status='retired',retired_at=NOW() WHERE status='active' AND id<>$1",
                version_id,
            )
            row = await conn.fetchrow(
                "UPDATE ocr_classifier_versions SET status='active',published_at=NOW(),retired_at=NULL "
                "WHERE id=$1 RETURNING id,status,parent_version_id,policy_json,metrics_json,created_at,prepared_at,published_at",
                version_id,
            )
            await conn.execute(
                "INSERT INTO app_settings(key,value,updated_at) VALUES('ocr_classifier_engine','legacy',NOW()) "
                "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()"
            )
    return _version(row)


@router.post("/rollback-to-legacy")
async def rollback_to_legacy() -> dict:
    result = await _pool().execute(
        "UPDATE ocr_classifier_versions SET status='retired',retired_at=NOW() "
        "WHERE status='active'"
    )
    await _pool().execute(
        "INSERT INTO app_settings(key,value,updated_at) VALUES('ocr_classifier_engine','legacy',NOW()) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()"
    )
    return {"ok": True, "retired": int(result.rsplit(" ", 1)[-1])}


@router.post("/versions/{version_id}/shadow")
async def run_shadow(version_id: int, limit: int = 100) -> dict:
    pool = _pool()
    version = await pool.fetchrow(
        "SELECT model_json FROM ocr_classifier_versions WHERE id=$1 AND status IN ('tested','ready','active','retired')",
        version_id,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="可测试版本不存在")
    model = _json(version["model_json"], {})
    rows = await pool.fetch(
        "SELECT o.id,COALESCE(NULLIF(o.normalized_text,''),o.raw_text,'') AS text,o.classifier_signals_json "
        "FROM listing_media_ocr o WHERE o.status IN ('done','failed') "
        "AND NOT EXISTS (SELECT 1 FROM ocr_classification_labels l WHERE l.ocr_id=o.id AND l.superseded_at IS NULL) "
        "ORDER BY o.id DESC LIMIT $1",
        max(1, min(limit, 500)),
    )
    values = []
    for row in rows:
        predicted, confidence, _ = predict(
            model, row["text"], _json(row["classifier_signals_json"], {}),
        )
        values.append((version_id, row["id"], predicted, confidence))
    async with pool.acquire() as conn:
        async with conn.transaction():
            for value in values:
                await conn.execute(
                    "INSERT INTO ocr_classifier_shadow_predictions(version_id,ocr_id,predicted_class,confidence) "
                    "VALUES($1,$2,$3,$4) ON CONFLICT(version_id,ocr_id) DO UPDATE SET "
                    "predicted_class=EXCLUDED.predicted_class,confidence=EXCLUDED.confidence,created_at=NOW()",
                    *value,
                )
    return {"version_id": version_id, "evaluated": len(values), "active_rules_unchanged": True}


@router.get("/versions/{version_id}/shadow")
async def shadow_summary(version_id: int) -> dict:
    rows = await _pool().fetch(
        "SELECT p.predicted_class,count(*) AS predicted_count,round(avg(p.confidence),4) AS average_confidence, "
        "count(label.category_code) AS reviewed_count, "
        "count(*) FILTER (WHERE label.category_code=p.predicted_class) AS correct_count "
        "FROM ocr_classifier_shadow_predictions p "
        "LEFT JOIN LATERAL (SELECT category_code FROM ocr_classification_labels l "
        " WHERE l.ocr_id=p.ocr_id AND l.superseded_at IS NULL ORDER BY l.created_at DESC,l.id DESC LIMIT 1) label ON TRUE "
        "WHERE p.version_id=$1 GROUP BY p.predicted_class ORDER BY p.predicted_class",
        version_id,
    )
    items = [dict(row) for row in rows]
    reviewed = sum(row["reviewed_count"] for row in items)
    correct = sum(row["correct_count"] for row in items)
    return {
        "version_id": version_id,
        "predicted_count": sum(row["predicted_count"] for row in items),
        "reviewed_count": reviewed,
        "correct_count": correct,
        "accuracy": round(correct / reviewed, 4) if reviewed else None,
        "items": items,
    }


@router.post("/versions/{version_id}/reclassify")
async def reclassify_selected(version_id: int, body: ReclassifyBody) -> dict:
    if not body.ids:
        raise HTTPException(status_code=422, detail="请选择要重新分类的图片")
    pool = _pool()
    if await _selected_engine(pool) == "vision4":
        raise HTTPException(status_code=409, detail="当前使用 V4，请先切换到旧版模型再使用旧版重新分类")
    version = await pool.fetchrow(
        "SELECT model_json,policy_json FROM ocr_classifier_versions "
        "WHERE id=$1 AND status IN ('ready','active','retired')",
        version_id,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="版本不存在或尚未准备上线")
    model = _json(version["model_json"], {})
    rows = await pool.fetch(
        "SELECT id,COALESCE(NULLIF(normalized_text,''),raw_text,'') AS text,classifier_signals_json "
        "FROM listing_media_ocr WHERE id=ANY($1::bigint[]) ORDER BY id",
        body.ids,
    )
    updated = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for row in rows:
                label, confidence, _ = predict(
                    model, row["text"], _json(row["classifier_signals_json"], {}),
                )
                await conn.execute(
                    "UPDATE listing_media_ocr SET predicted_classification=$2,predicted_confidence=$3, "
                    "classifier_version_id=$4,predicted_model_version=NULL,predicted_model_sha=NULL, "
                    "predicted_score_semantics=NULL,updated_at=NOW() WHERE id=$1",
                    row["id"], label, confidence, version_id,
                )
                updated += 1
    return {"version_id": version_id, "updated": updated, "human_labels_preserved": True}
