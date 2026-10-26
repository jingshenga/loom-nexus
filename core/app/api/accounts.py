"""Gateway account administration: list, login workflow, disconnect.

The login workflow is fully remote (accounts admin page): core inserts
a login_requests row, the bridge login-watcher spawns the Telethon
login worker, the worker flips state to code_sent (or password_needed)
and the page submits the secret through here.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, sqlres
from app.api.auth import require_admin

router = APIRouter(
    prefix="/api/admin/accounts", tags=["admin-accounts"],
    dependencies=[Depends(require_admin)],
)

SQL_LIST = sqlres.load("admin_accounts.sql")
SQL_DISCONNECT = sqlres.load("account_disconnect.sql")
SQL_ACTIVATE = sqlres.load("account_activate.sql")
SQL_LOGIN_START = sqlres.load("login_start.sql")
SQL_LOGIN_SECRET = sqlres.load("login_set_secret.sql")
SQL_LOGIN_LATEST = sqlres.load("login_latest.sql")

SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SAFE_PHONE = re.compile(r"^\+?[0-9]{5,20}$")


class LoginBody(BaseModel):
    phone: str
    session_name: str = ""


class SecretBody(BaseModel):
    request_id: int
    secret: str
    is_password: bool = False


def _pool():
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


@router.get("")
async def list_accounts() -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_LIST)
    latest = await pool.fetch(SQL_LOGIN_LATEST)
    return {
        "items": [
            {
                "id": r["account_id"],
                "account_uid": r["account_uid"],
                "display_name": r["display_name"],
                "username": r["username"],
                "session_file": r["session_file"],
                "status": r["status"],
                "open_sessions": r["open_sessions"],
                "bound_sources": r["bound_sources"],
                "last_active_at": (
                    r["last_active_at"].isoformat() if r["last_active_at"] else None
                ),
            }
            for r in rows
        ],
        "login": (
            {
                "id": latest[0]["id"],
                "phone": latest[0]["phone"],
                "session_name": latest[0]["session_name"],
                "state": latest[0]["state"],
                "error": latest[0]["error"],
            }
            if latest
            else None
        ),
    }


@router.post("/login")
async def start_login(body: LoginBody) -> dict:
    if not SAFE_PHONE.match(body.phone or ""):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    name = (body.session_name or "").strip()
    if name and not SAFE_NAME.match(name):
        raise HTTPException(
            status_code=400,
            detail="会话名只允许字母/数字/横线/下划线（≤32 字符）",
        )
    pool = _pool()
    try:
        rows = await pool.fetch(SQL_LOGIN_START, body.phone, name)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="已有进行中的登录流程" if "already in progress" in str(exc)
            else str(exc)[:120],
        )
    r = rows[0]
    return {
        "request_id": r["request_id"],
        "session_name": r["session_name"],
        "state": r["state"],
    }


@router.post("/login/secret")
async def submit_secret(body: SecretBody) -> dict:
    if not (body.secret or "").strip():
        raise HTTPException(status_code=400, detail="empty secret")
    pool = _pool()
    try:
        rows = await pool.fetch(
            SQL_LOGIN_SECRET, body.request_id, body.secret.strip(),
            body.is_password,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:160])
    return {"ok": True, "state": rows[0]["state"]}


@router.post("/{account_id}/disconnect")
async def disconnect_account(account_id: int) -> dict:
    """Take a gateway offline: closes its open chat sessions; bound
    collection sources stay configured but stop collecting."""
    pool = _pool()
    rows = await pool.fetch(SQL_DISCONNECT, account_id)
    r = rows[0]
    return {
        "ok": True,
        "closed_sessions": r["closed_sessions"],
        "bound_sources": r["bound_sources"],
    }


@router.post("/{account_id}/activate")
async def activate_account(account_id: int) -> dict:
    pool = _pool()
    rows = await pool.fetch(SQL_ACTIVATE, account_id)
    if not rows:
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True}
