"""Gateway account login.

Fully interactive (tty):
    python -m runner.login <name>

Daemon mode (driven remotely over ssh, no tty):
    python -m runner.login --daemon <name> <phone>

DB-driven daemon mode (driven from the accounts admin page):
    python -m runner.login --daemon-db <request_id>

Both daemon modes send the code request immediately, then wait for the
secret. The ssh mode polls two FIXED files inside the bind-mounted
session directory:
    data/sessions/login-pending.code     <- the verification code
    data/sessions/login-pending.password <- 2FA password (only if enabled)
The DB mode polls the login_requests row instead (code / password
columns, consumed exactly once). The session name is restricted to
[A-Za-z0-9_-]{1,32}; the pending files use constant names so no request
data ever shapes a path.
"""
import asyncio
import logging
import os
import re
import sys
import time

import asyncpg

from runner import sqlres
from runner.dbconn import connect_kwargs

logger = logging.getLogger("nexus-bridge.login")

SESSION_DIR = os.getenv("NEXUS_BRIDGE_SESSION_DIR", "/app/data/sessions")
CODE_FILE = os.path.join(SESSION_DIR, "login-pending.code")
PASSWORD_FILE = os.path.join(SESSION_DIR, "login-pending.password")
SQL_REGISTER = sqlres.load("register_account.sql")
SQL_LOGIN_GET = sqlres.load("login_get.sql")
SQL_LOGIN_STATE = sqlres.load("login_state.sql")
SQL_TAKE_CODE = sqlres.load("login_take_code.sql")
SQL_TAKE_PASSWORD = sqlres.load("login_take_password.sql")
SQL_LOGIN_CLAIM = sqlres.load("login_claim.sql")
WAIT_S = 600
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _safe_name(name: str) -> str:
    if not SAFE_NAME.match(name):
        raise SystemExit("invalid session name: alphanumerics, dash, underscore only")
    return name


def _build_client(name: str):
    from telethon import TelegramClient

    api_id = os.getenv("NEXUS_BRIDGE_API_ID", "")
    api_hash = os.getenv("NEXUS_BRIDGE_API_HASH", "")
    if not api_id or not api_hash:
        raise SystemExit("NEXUS_BRIDGE_API_ID / NEXUS_BRIDGE_API_HASH missing in .env")
    os.makedirs(SESSION_DIR, exist_ok=True)
    return TelegramClient(
        os.path.join(SESSION_DIR, name), int(api_id), api_hash,
    )


async def _wait_file(path: str, timeout_s: int = WAIT_S) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with open(path) as fh:
                value = fh.read().strip()
            if value:
                return value
        except FileNotFoundError:
            pass
        await asyncio.sleep(2)
    return ""


async def _register(me, name: str) -> None:
    conn = await asyncpg.connect(**connect_kwargs())
    try:
        rows = await conn.fetch(
            SQL_REGISTER, me.id, me.first_name or name, me.username, f"{name}.session",
        )
    finally:
        await conn.close()
    print(
        f"login ok: uid={me.id} account_row={rows[0]['account_id']} "
        f"session={name}.session"
    )


async def daemon_login(name: str, phone: str) -> int:
    """One process: send code -> await code file -> sign in (2FA aware)."""
    name = _safe_name(name)
    client = _build_client(name)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        print(f"CODE_SENT phone={phone}", flush=True)
        code = await _wait_file(CODE_FILE)
        if not code:
            print("TIMEOUT waiting for code file")
            return 3
        print(f"CODE_RECEIVED len={len(code)}", flush=True)
        try:
            await client.sign_in(
                phone=phone, code=code, phone_code_hash=sent.phone_code_hash,
            )
        except Exception as exc:
            if type(exc).__name__ == "SessionPasswordNeededError":
                print("2FA_PASSWORD_NEEDED", flush=True)
                password = await _wait_file(PASSWORD_FILE)
                if not password:
                    print("TIMEOUT waiting for password file")
                    return 3
                await client.sign_in(password=password)
            else:
                raise
        me = await client.get_me()
    finally:
        for path in (CODE_FILE, PASSWORD_FILE):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        await client.disconnect()
    await _register(me, name)
    return 0


async def interactive(name: str) -> int:
    name = _safe_name(name)
    client = _build_client(name)
    await client.start()
    me = await client.get_me()
    await client.disconnect()
    await _register(me, name)
    return 0


async def _wait_secret(conn, sql, req_id: int, timeout_s: int = WAIT_S) -> str:
    """Poll a login_requests secret column; consume it exactly once."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rows = await conn.fetch(sql, req_id)
        if rows and rows[0]["secret"]:
            return str(rows[0]["secret"])
        await asyncio.sleep(2)
    return ""


async def db_login(req_id: int) -> int:
    """Accounts-page driven login: state transitions live in
    login_requests, secrets are consumed exactly once."""
    conn = await asyncpg.connect(**connect_kwargs())
    try:
        rows = await conn.fetch(SQL_LOGIN_GET, req_id)
        if not rows:
            print(f"login request {req_id} not found")
            return 4
        name = _safe_name(rows[0]["session_name"])
        phone = rows[0]["phone"]
        client = _build_client(name)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
            await conn.fetch(SQL_LOGIN_STATE, req_id, "code_sent", None)
            code = await _wait_secret(conn, SQL_TAKE_CODE, req_id)
            if not code:
                await conn.fetch(
                    SQL_LOGIN_STATE, req_id, "failed", "timeout waiting for code",
                )
                return 3
            try:
                await client.sign_in(
                    phone=phone, code=code, phone_code_hash=sent.phone_code_hash,
                )
            except Exception as exc:
                if type(exc).__name__ == "SessionPasswordNeededError":
                    await conn.fetch(
                        SQL_LOGIN_STATE, req_id, "password_needed", None,
                    )
                    password = await _wait_secret(
                        conn, SQL_TAKE_PASSWORD, req_id,
                    )
                    if not password:
                        await conn.fetch(
                            SQL_LOGIN_STATE, req_id, "failed",
                            "timeout waiting for 2FA password",
                        )
                        return 3
                    await client.sign_in(password=password)
                else:
                    raise
            me = await client.get_me()
        finally:
            await client.disconnect()
        await _register(me, name)
        await conn.fetch(SQL_LOGIN_STATE, req_id, "done", None)
        return 0
    except Exception as exc:
        try:
            await conn.fetch(SQL_LOGIN_STATE, req_id, "failed", str(exc)[:200])
        except Exception:
            pass
        print(f"login failed: {exc}")
        return 1
    finally:
        await conn.close()


async def _reap(proc, pool, req_id: int) -> None:
    rc = await proc.wait()
    if rc != 0:
        rows = await pool.fetch(SQL_LOGIN_GET, req_id)
        if rows and rows[0]["state"] not in ("done", "failed"):
            await pool.fetch(
                SQL_LOGIN_STATE, req_id, "failed", f"worker exited rc={rc}",
            )
            logger.warning("login worker rc=%s for request %s", rc, req_id)


async def run_login_watcher(pool, stop: asyncio.Event) -> None:
    """Spawn a DB-driven login worker whenever a request pends."""
    while not stop.is_set():
        try:
            rows = await pool.fetch(SQL_LOGIN_CLAIM)
            req_id = rows[0]["request_id"] if rows else None
            if req_id is not None:
                logger.info("login request %s claimed; spawning worker", req_id)
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "runner.login",
                    "--daemon-db", str(req_id),
                )
                asyncio.create_task(_reap(proc, pool, req_id))
        except Exception:
            logger.exception("login watcher cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) == 1 and not argv[0].startswith("--"):
        return asyncio.run(interactive(argv[0]))
    if len(argv) == 3 and argv[0] == "--daemon":
        return asyncio.run(daemon_login(argv[1], argv[2]))
    if len(argv) == 2 and argv[0] == "--daemon-db":
        return asyncio.run(db_login(int(argv[1])))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
