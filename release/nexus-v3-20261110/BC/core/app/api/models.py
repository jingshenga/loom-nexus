"""Administrator model inventory and guarded Vision4 package releases."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app import db
from app.api.auth import current_user_id, require_admin
from app.config import get_settings
from app.extractor.media_ocr import ENGINE_VERSION
from app.extractor.vision4_classifier import Vision4Classifier

router = APIRouter(
    prefix="/api/admin/models", tags=["admin-models"],
    dependencies=[Depends(require_admin)],
)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
MAX_FILES = 8
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


def _store_dir() -> Path:
    path = Path(get_settings().vision4_model_store_dir).resolve()
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    return path


def _allowed_member(name: str) -> bool:
    return name in {
        "manifest.json", "labels.json", "preprocess.json", "model.onnx",
        "model.onnx.manifest.json", "SHA256SUMS",
    }


def _load_classifier(model_dir: Path) -> tuple[Vision4Classifier, dict]:
    try:
        manifest = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("模型 manifest 无法读取") from exc
    version = manifest.get("model_version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("模型版本标识格式无效")
    classifier = Vision4Classifier("active", str(model_dir))
    try:
        classifier._ensure_loaded()
        with tempfile.TemporaryDirectory(prefix="nexus-model-smoke-") as tmp:
            from PIL import Image

            sample = Path(tmp) / "sample.png"
            Image.new("RGB", (64, 64), (128, 128, 128)).save(sample)
            result = classifier.predict(str(sample))
            if result.get("status") != "classified":
                raise ValueError("模型合成图片推理未通过")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("模型加载或合成图片推理失败") from exc
    return classifier, manifest


async def _read_upload(request: Request, target: Path) -> None:
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="模型包超过 100 MiB 限制")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length 无效") from exc
    total = 0
    with target.open("xb") as output:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="模型包超过 100 MiB 限制")
            output.write(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="模型包为空")


def _unpack_package(archive: Path, target: Path) -> dict:
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if not 1 <= len(members) <= MAX_FILES:
                raise ValueError("模型包文件数量超出限制")
            total = 0
            seen = set()
            for item in members:
                path = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if (not _allowed_member(item.filename) or path.is_absolute()
                        or ".." in path.parts or len(path.parts) != 1
                        or stat.S_ISLNK(mode) or item.is_dir()):
                    raise ValueError("模型包包含不支持的路径或文件类型")
                if item.filename in seen:
                    raise ValueError("模型包包含重复文件名")
                seen.add(item.filename)
                total += item.file_size
                if item.file_size < 0 or total > MAX_PACKAGE_BYTES:
                    raise ValueError("模型包展开后超过 200 MiB 限制")
            required = {"manifest.json", "labels.json", "preprocess.json", "model.onnx",
                        "model.onnx.manifest.json", "SHA256SUMS"}
            if not required.issubset(seen):
                raise ValueError("模型包缺少必要清单或模型文件")
            for item in members:
                with bundle.open(item) as source, (target / item.filename).open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except zipfile.BadZipFile as exc:
        raise ValueError("上传文件不是有效 ZIP 模型包") from exc
    _, manifest = _load_classifier(target)
    return manifest


@router.get("")
async def model_inventory(request: Request) -> dict:
    pool = _pool()
    settings = get_settings()
    processor = getattr(request.app.state, "media_ocr_processor", None)
    classifier = processor._vision4 if processor else None
    active = await pool.fetchrow(
        "SELECT id,version_key,model_path,model_sha256,status,metadata_json,created_at,activated_at "
        "FROM vision4_model_versions WHERE status='active' ORDER BY activated_at DESC,id DESC LIMIT 1"
    )
    installed = await pool.fetch(
        "SELECT id,version_key,model_sha256,status,metadata_json,created_at,activated_at "
        "FROM vision4_model_versions ORDER BY created_at DESC,id DESC LIMIT 30"
    )
    events = await pool.fetch(
        "SELECT e.id,e.model_version_id,v.version_key,e.action,e.actor_id,e.details_json,e.created_at "
        "FROM vision4_model_events e LEFT JOIN vision4_model_versions v ON v.id=e.model_version_id "
        "ORDER BY e.created_at DESC,e.id DESC LIMIT 30"
    )
    ocr = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE status='done') AS done, "
        "count(*) FILTER (WHERE status='failed') AS failed, max(updated_at) AS last_processed_at "
        "FROM listing_media_ocr"
    )
    active_manifest = getattr(classifier, "_manifest", None) if classifier else None
    return {
        "vision4": {
            "mode": settings.vision4_mode,
            "runtime_status": "loaded" if classifier and classifier._session is not None
                else "error" if classifier and classifier._load_error else "not_loaded",
            "runtime_error": classifier._load_error if classifier else None,
            "version": active_manifest.get("model_version") if active_manifest else
                (active["version_key"] if active else Path(settings.vision4_model_dir).name),
            "sha256": active_manifest.get("model_sha256") if active_manifest else
                (active["model_sha256"] if active else None),
            "model_path": str(active["model_path"]) if active else settings.vision4_model_dir,
            "categories": active_manifest.get("labels", []) if active_manifest else [],
            "preprocessing": active_manifest.get("preprocessing", {}) if active_manifest else {},
            "installed": [dict(row) for row in installed],
            "events": [dict(row) for row in events],
        },
        "ocr_classifier": {"endpoint": "/admin/ocr-classifier", "api": "/api/admin/ocr-classifier/versions"},
        "rapidocr": {
            "engine_version": ENGINE_VERSION,
            "runtime_status": "available",
            "done_count": ocr["done"],
            "failed_count": ocr["failed"],
            "last_processed_at": ocr["last_processed_at"].isoformat() if ocr["last_processed_at"] else None,
        },
    }


@router.post("/vision4/upload", status_code=201)
async def upload_vision4_model(
    request: Request, authorization: str = Header(default=""),
) -> dict:
    actor = current_user_id(authorization)
    store = _store_dir()
    with tempfile.TemporaryDirectory(prefix=".upload-", dir=store) as tmp:
        temp = Path(tmp)
        archive = temp / "upload.zip"
        try:
            await _read_upload(request, archive)
            package_dir = temp / "package"
            package_dir.mkdir(mode=0o750)
            manifest = _unpack_package(archive, package_dir)
            version = manifest["model_version"]
            destination = store / version
            if destination.exists() or destination.is_symlink():
                raise HTTPException(status_code=409, detail="该模型版本已存在，不能覆盖")
            model_sha = manifest["model_sha256"]
            os.replace(package_dir, destination)
        except HTTPException:
            raise
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        async with _pool().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "INSERT INTO vision4_model_versions(version_key,model_path,model_sha256,status,metadata_json,created_by) "
                    "VALUES($1,$2,$3,'ready',$4::jsonb,$5) "
                    "RETURNING id,version_key,model_sha256,status,created_at",
                    version, str(destination), model_sha, json.dumps(manifest, ensure_ascii=False), actor,
                )
                await conn.execute(
                    "INSERT INTO vision4_model_events(model_version_id,action,actor_id,details_json) "
                    "VALUES($1,'installed',$2,$3::jsonb)", row["id"], actor,
                    json.dumps({"sha256": model_sha}),
                )
    except Exception as exc:
        shutil.rmtree(destination, ignore_errors=True)
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="该版本标识已登记") from exc
        raise
    return dict(row)


async def _activate_version(version_id: int, request: Request, actor: int, action: str) -> dict:
    settings = get_settings()
    if settings.vision4_mode != "active":
        raise HTTPException(status_code=409, detail="Vision4 当前不是 active 模式，拒绝切换模型")
    processor = getattr(request.app.state, "media_ocr_processor", None)
    if processor is None:
        raise HTTPException(status_code=503, detail="OCR 处理器尚未就绪")
    pool = _pool()
    row = await pool.fetchrow(
        "SELECT id,version_key,model_path,model_sha256,status FROM vision4_model_versions WHERE id=$1",
        version_id,
    )
    if row is None or row["status"] not in {"ready", "rollback", "active"}:
        raise HTTPException(status_code=404, detail="可用模型版本不存在")
    model_path = Path(row["model_path"]).resolve()
    if model_path.parent != _store_dir() or model_path.is_symlink() or not model_path.is_dir():
        raise HTTPException(status_code=409, detail="模型目录不在受控版本目录中")
    try:
        replacement, _ = await asyncio.to_thread(_load_classifier, model_path)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="新模型加载或测试推理失败，当前模型未变更") from exc

    async with processor._vision4_gate:
        previous = processor._vision4
        previous_manifest = previous._manifest or {}
        processor._vision4 = replacement
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    if previous_manifest.get("model_sha256") and previous.model_dir.exists():
                        old_version = previous_manifest.get("model_version") or previous.model_dir.name
                        await conn.execute(
                            "INSERT INTO vision4_model_versions(version_key,model_path,model_sha256,status,metadata_json) "
                            "VALUES($1,$2,$3,'rollback',$4::jsonb) ON CONFLICT(version_key) DO UPDATE "
                            "SET status='rollback',model_path=EXCLUDED.model_path,model_sha256=EXCLUDED.model_sha256",
                            old_version, str(previous.model_dir), previous_manifest["model_sha256"],
                            json.dumps(previous_manifest, ensure_ascii=False),
                        )
                    await conn.execute(
                        "UPDATE vision4_model_versions SET status='rollback' WHERE status='active' AND id<>$1",
                        version_id,
                    )
                    await conn.execute(
                        "UPDATE vision4_model_versions SET status='active',activated_at=NOW() WHERE id=$1",
                        version_id,
                    )
                    await conn.execute(
                        "INSERT INTO vision4_model_events(model_version_id,action,actor_id,details_json) "
                        "VALUES($1,$2,$3,$4::jsonb)", version_id, action, actor,
                        json.dumps({"previous_sha256": previous_manifest.get("model_sha256")}),
                    )
        except Exception:
            processor._vision4 = previous
            raise
    return {"ok": True, "version": row["version_key"], "sha256": row["model_sha256"], "action": action}


@router.post("/{version_id}/activate")
async def activate_vision4_model(version_id: int, request: Request, authorization: str = Header(default="")) -> dict:
    return await _activate_version(version_id, request, current_user_id(authorization), "activated")


@router.post("/{version_id}/rollback")
async def rollback_vision4_model(version_id: int, request: Request, authorization: str = Header(default="")) -> dict:
    return await _activate_version(version_id, request, current_user_id(authorization), "rolled_back")
