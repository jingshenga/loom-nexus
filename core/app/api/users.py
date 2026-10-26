"""Site user administration (admin only): create, list, password,
role, enable/disable, delete."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, sqlres
from app.api.auth import hash_password, require_admin

router = APIRouter(
    prefix="/api/admin/users", tags=["admin-users"],
    dependencies=[Depends(require_admin)],
)

SQL_LIST = sqlres.load("user_list.sql")
SQL_CREATE = sqlres.load("user_create.sql")
SQL_SET_PASSWORD = sqlres.load("user_set_password.sql")
SQL_SET_ADMIN = sqlres.load("user_set_admin.sql")
SQL_SET_ACTIVE = sqlres.load("user_set_active.sql")
SQL_DELETE = sqlres.load("user_delete.sql")


class UserBody(BaseModel):
    username: str
    password: str
    display_name: str = ""
    is_admin: bool = False


class PasswordBody(BaseModel):
    password: str


class FlagBody(BaseModel):
    value: bool


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


@router.get("")
async def list_users() -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_LIST)
    return {
        "items": [
            {
                "id": r["user_id"],
                "username": r["username"],
                "display_name": r["display_name"],
                "is_admin": r["is_admin"],
                "is_active": r["is_active"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "last_login_at": (
                    r["last_login_at"].isoformat() if r["last_login_at"] else None
                ),
            }
            for r in rows
        ]
    }


@router.post("")
async def create_user(body: UserBody) -> dict:
    username = (body.username or "").strip().lower()
    if len(body.password or "") < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    display_name = (body.display_name or "").strip()
    if not display_name:
        raise HTTPException(
            status_code=400,
            detail="显示名必填（站内咨询首条消息会以此向对方表明身份）",
        )
    pool = _pool()
    try:
        rows = await pool.fetch(
            SQL_CREATE, username, hash_password(body.password),
            display_name, body.is_admin,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:140])
    return {"user_id": rows[0]["user_id"]}


@router.post("/{user_id}/password")
async def set_password(user_id: int, body: PasswordBody) -> dict:
    if len(body.password or "") < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    pool = _pool()
    rows = await pool.fetch(SQL_SET_PASSWORD, user_id, hash_password(body.password))
    if not rows[0]["ok"]:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@router.post("/{user_id}/admin")
async def set_admin(user_id: int, body: FlagBody) -> dict:
    pool = _pool()
    try:
        rows = await pool.fetch(SQL_SET_ADMIN, user_id, body.value)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:140])
    if not rows[0]["ok"]:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@router.post("/{user_id}/active")
async def set_active(user_id: int, body: FlagBody) -> dict:
    pool = _pool()
    try:
        rows = await pool.fetch(SQL_SET_ACTIVE, user_id, body.value)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:140])
    if not rows[0]["ok"]:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@router.delete("/{user_id}")
async def delete_user(user_id: int) -> dict:
    pool = _pool()
    try:
        rows = await pool.fetch(SQL_DELETE, user_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:140])
    if not rows[0]["ok"]:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True, "closed_sessions": rows[0]["open_sessions"]}
