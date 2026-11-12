"""Site-wide account auth: pbkdf2 passwords + HMAC time-boxed tokens.

Every visitor logs in with a site account (created on the users admin
page). The bearer token carries the user id and admin flag:
"<exp>.<uid>.<admin 0|1>.<hmac(secret, "exp.uid.admin")>" — the signing
secret is the gateway admin password from the environment, so rotating
it invalidates all outstanding tokens. No cookies, no server sessions.

API surfaces:
    require_user  — any active account (square, favorites, chat, media)
    require_admin — accounts flagged is_admin (management pages)
"""
import hashlib
import hmac
import logging
import secrets
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app import db, sqlres
from app.config import get_settings

logger = logging.getLogger("nexus-core.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

TOKEN_TTL_S = 12 * 3600
PBKDF2_ITERATIONS = 120_000
SQL_USER_AUTH = sqlres.load("user_get_auth.sql")
SQL_TOUCH = sqlres.load("user_touch_login.sql")


# ---------- password hashing ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, PBKDF2_ITERATIONS,
    )
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------- tokens ----------

def _secret() -> str:
    return get_settings().admin_password


def _sign(payload: str) -> str:
    return hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(user_id: int, is_admin: bool) -> tuple:
    exp = int(time.time()) + TOKEN_TTL_S
    payload = f"{exp}.{user_id}.{1 if is_admin else 0}"
    return f"{payload}.{_sign(payload)}", exp


def verify_token(token: str):
    """Return (user_id, is_admin) for a valid unexpired token, else None."""
    try:
        exp, uid, admin, sig = token.split(".")
        if int(exp) < time.time():
            return None
        payload = f"{exp}.{uid}.{admin}"
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        return int(uid), admin == "1"
    except (ValueError, TypeError):
        return None


def bearer_identity(authorization: str):
    if not authorization.startswith("Bearer "):
        return None
    return verify_token(authorization[len("Bearer "):])


async def require_user(authorization: str = Header(default="")) -> None:
    if bearer_identity(authorization) is None:
        raise HTTPException(status_code=401, detail="login required")


async def require_admin(authorization: str = Header(default="")) -> None:
    identity = bearer_identity(authorization)
    if identity is None or not identity[1]:
        raise HTTPException(status_code=401, detail="admin token required")


def current_user_id(authorization: str) -> int:
    identity = bearer_identity(authorization)
    if identity is None:
        raise HTTPException(status_code=401, detail="login required")
    return identity[0]


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


@router.post("/login")
async def login(body: LoginBody) -> dict:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    username = (body.username or "").strip().lower()
    rows = await pool.fetch(SQL_USER_AUTH, username)
    if not rows:
        raise HTTPException(status_code=401, detail="账户或密码错误")
    r = rows[0]
    if not r["is_active"] or not verify_password(
        body.password or "", r["password_hash"],
    ):
        raise HTTPException(status_code=401, detail="账户或密码错误")
    await pool.fetch(SQL_TOUCH, r["user_id"])
    token, exp = issue_token(r["user_id"], r["is_admin"])
    return {
        "token": token,
        "expires_at": exp,
        "user_id": r["user_id"],
        "username": username,
        "is_admin": r["is_admin"],
    }


@router.post("/change-password")
async def change_password(
    body: LoginBody, authorization: str = Header(default="")
) -> dict:
    """Signed-in users rotate their own password."""
    identity = bearer_identity(authorization)
    if identity is None:
        raise HTTPException(status_code=401, detail="login required")
    if len(body.password or "") < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    await pool.fetch(
        sqlres.load("user_set_password.sql"), identity[0],
        hash_password(body.password),
    )
    return {"ok": True}


async def ensure_admin_account() -> None:
    """Bootstrap at startup: when no site account exists yet, mint the
    first admin (username "admin") from the environment admin password."""
    pool = db.get_pool()
    if pool is None:
        return
    try:
        rows = await pool.fetch(sqlres.load("user_list.sql"))
        if rows:
            return
        await pool.fetch(
            sqlres.load("user_create.sql"),
            "admin", hash_password(get_settings().admin_password),
            "管理员", True,
        )
        logger.info("bootstrapped first site account: admin (from env password)")
    except Exception as exc:
        logger.warning("admin account bootstrap skipped: %s", exc)
