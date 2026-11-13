"""Outbound message dispatch: polls the local outbox and delivers via
the assigned gateway account.

SIM mode registers two virtual accounts and fake-delivers with a
synthetic reply, so the whole outbox loop (claim -> deliver -> finish
-> inbound store) runs without external network. Real mode sends
through Telethon clients kept alive for each logged-in account, with:

- identity prefix: every outbound message carries the alias snapshotted
  when it entered the outbox;
- failure classification: Telethon errors map to friendly text shown
  (admin-only) next to the message;
- stranger-limit handling: once an account trips Telegram's
  cannot-message-strangers restriction, its day flag flips to
  bot-first — brand-new sessions go through the contact's own bot
  (extracted from the listing text) while sessions the contact has
  ever replied to keep direct delivery;
- bot fallback: permanent direct failures fall back to that bot once
  before the normal retry/failed path takes over.
"""
import asyncio
import io
import logging
import os
import random
import re

import asyncpg
from PIL import Image

from runner import sqlres

logger = logging.getLogger("nexus-bridge.sender")

SQL_CLAIM = sqlres.load("outbox_claim.sql")
SQL_FINISH = sqlres.load("outbox_finish.sql")
SQL_INCOMING = sqlres.load("chat_incoming.sql")
SQL_REGISTER = sqlres.load("register_account.sql")
SQL_VERIFIED_BOT = sqlres.load("verified_bot_for_session.sql")
SQL_SET_CHANNEL = sqlres.load("session_set_channel.sql")
SQL_LIMIT_SET = sqlres.load("gateway_limit_set.sql")

SIM_ACCOUNTS = ((6660001, "sim-alpha"), (6660002, "sim-beta"))
POLL_S = float(os.getenv("NEXUS_BRIDGE_SEND_INTERVAL", "2"))

BOT_RE = re.compile(r"(?:@|t\.me/)([A-Za-z0-9_]{4,32}bot)\b", re.IGNORECASE)

# a send that gets no ACK in 45s is treated as failed (the proxy path
# occasionally stalls a request forever); the retry cycle resends it
SEND_TIMEOUT_S = 45


def classify_error(exc) -> tuple:
    """(kind, friendly_text) for a Telegram send failure."""
    name = type(exc).__name__
    if name == "TimeoutError" or isinstance(exc, asyncio.TimeoutError):
        return "timeout", "发送超时（网络无响应，自动重试）"
    if name in ("UserIsBlockedError", "ChatWriteForbiddenError"):
        return "blocked", "对方已屏蔽网关账号"
    if name == "UserPrivacyRestrictedError":
        return "privacy", "对方隐私设置不允许陌生人私聊"
    if name == "PeerFloodError":
        return "flood", "触发官方陌生人消息限制"
    if name == "FloodWaitError":
        return "flood", f"发送过快，官方要求等待 {getattr(exc, 'seconds', '?')} 秒"
    if name in ("UserDeactivatedBanError", "AuthKeyUnregisteredError"):
        return "gone", "对方账号可能已注销"
    if isinstance(exc, ValueError):
        return "gone", "找不到对方账号（可能已注销）"
    return "other", str(exc)[:200]


async def _send_dm(client, target, content):
    return await asyncio.wait_for(
        client.send_message(target, content), timeout=SEND_TIMEOUT_S,
    )


async def _send_any(client, target, kind, payload, caption=""):
    """Text or a named photo stream, both with the send timeout."""
    if kind == "photo":
        return await asyncio.wait_for(
            client.send_file(target, payload, caption=caption, force_document=False),
            timeout=SEND_TIMEOUT_S,
        )
    return await _send_dm(client, target, payload)


async def _resolve_bot(pool, session_id: int):
    """Return only a Bot candidate explicitly verified by an administrator."""
    rows = await pool.fetch(SQL_VERIFIED_BOT, session_id)
    handles = {
        match.group(1).lower(): match.group(1)
        for row in rows
        if (match := BOT_RE.search((row["bot_url"] or "")))
    }
    return next(iter(handles.values())) if len(handles) == 1 else None


async def _sim_account_ids(pool: asyncpg.Pool) -> list:
    ids = []
    for uid, name in SIM_ACCOUNTS:
        rows = await pool.fetch(
            SQL_REGISTER, uid, f"{name}（模拟）", name, f"{name}.session",
        )
        ids.append(rows[0]["account_id"])
    return ids


async def _real_account_ids(pool: asyncpg.Pool) -> list:
    rows = await pool.fetch(sqlres.load("active_accounts.sql"))
    return [r["id"] for r in rows]


async def _finish(pool, job_id, ok, error, platform_msg_id, channel=None):
    rows = await pool.fetch(SQL_FINISH, job_id, ok, error, platform_msg_id, channel)
    outcome = rows[0]["outcome"]
    (logger.info if ok or outcome == "retry" else logger.warning)(
        "outbox job %s -> %s%s", job_id, outcome,
        f" ({channel})" if channel else "",
    )


async def _send_sim(pool, account_id, job) -> None:
    await asyncio.sleep(0.6)
    fake_platform_id = random.randint(1_000_000, 9_999_999)
    await _finish(pool, job["job_id"], True, None, fake_platform_id, "direct")
    rows = await pool.fetch(
        SQL_INCOMING, account_id, job["contact_peer_id"],
        job.get("contact_username"), fake_platform_id,
        "（模拟回复）已收到您的咨询，稍后为您介绍。", None, None,
    )
    if rows[0]["message_id"] is not None:
        logger.info(
            "sim reply stored (contact=%s)", job["contact_peer_id"],
        )


def _payload(job):
    """Decode and validate a stored photo before handing it to Telegram."""
    import base64

    b64 = job.get("media_b64")
    if b64:
        if "," in b64 and b64.startswith("data:"):
            b64 = b64.split(",", 1)[1]  # strip the data-url header
        b64 += "=" * (-len(b64) % 4)    # tolerate missing padding
        try:
            raw = base64.b64decode(b64, validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(image.format)
            if not extension:
                raise ValueError("unsupported image format")
        except (ValueError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("invalid photo data") from exc
        photo = io.BytesIO(raw)
        photo.name = f"photo.{extension}"
        return ("photo", photo)
    return ("text", _prefixed_text(job))


def _prefixed_text(job) -> str:
    content = job["content"] or ""
    alias = (job.get("display_name") or "").strip()
    if not alias:
        return content
    content = re.sub(r"^【[^】]{1,24}】\s*", "", content, count=1)
    return f"【{alias}】{content}"


def _prefixed(job) -> str:
    """Return the stored alias-prefixed Telegram text for any message."""
    return _prefixed_text(job)


async def _send_real(pool, account_id, job) -> None:
    rows = await pool.fetch(
        sqlres.load("session_file_of.sql"), account_id,
    )
    if not rows:
        await _finish(pool, job["job_id"], False, "account row missing", None)
        return
    from runner import hub

    session_name = str(rows[0]["session_file"]).removesuffix(".session")
    client = await hub.get_client(session_name)
    if client is None:
        await _finish(pool, job["job_id"], False, "account unauthorized", None)
        return

    try:
        kind, payload = _payload(job)
    except ValueError:
        await _finish(pool, job["job_id"], False, "图片数据损坏，请重新拍摄发送", None)
        return
    # channel decision: pinned bot channel wins; otherwise a brand-new
    # session on an account with today's stranger-limit flag goes
    # bot-first, while any session the contact ever replied to (or one
    # with a cached peer id) stays direct
    use_bot = job["delivery_channel"] == "bot"
    if not use_bot and not job["has_inbound"] and not job["contact_peer_id"] \
            and job["limit_mode"] == "bot_first":
        use_bot = True

    bot = None
    if use_bot:
        bot = await _resolve_bot(pool, job["session_id"])
        if bot:
            await pool.fetch(
                SQL_SET_CHANNEL, job["session_id"], "bot", bot,
            )
    if not bot:
        # wanted bot-first but the listing text has no bot handle:
        # best-effort direct delivery
        use_bot = False

    target = bot if use_bot else (job["contact_peer_id"] or job["contact_username"])
    if not target:
        await _finish(
            pool, job["job_id"], False,
            "contact has neither peer id nor username", None,
        )
        return

    msg = None
    try:
        msg = await _send_any(client, target, kind, payload,
                              caption=_prefixed_text(job) if kind == "photo" else "")
    except Exception as exc:
        error_kind, friendly = classify_error(exc)
        if error_kind in ("flood", "timeout"):
            # today this account messages strangers through bots only
            await pool.fetch(SQL_LIMIT_SET, account_id, "bot_first")
        if not use_bot and error_kind in ("blocked", "privacy", "flood", "gone", "timeout"):
            bot = await _resolve_bot(pool, job["session_id"])
            if bot:
                try:
                    if kind == "photo":
                        payload.seek(0)
                    msg = await _send_any(client, bot, kind, payload,
                                          caption=_prefixed_text(job) if kind == "photo" else "")
                    use_bot = True
                    logger.warning(
                        "direct delivery failed (%s), fell back to bot %s",
                        friendly, bot,
                    )
                except Exception as exc2:
                    _, friendly = classify_error(exc2)
        if msg is None:
            await _finish(pool, job["job_id"], False, friendly, None)
            return

    peer_user = getattr(msg, "peer_id", None)
    user_id = getattr(peer_user, "user_id", None)
    if hasattr(user_id, "value"):  # TL long
        user_id = user_id.value
    if user_id and not use_bot:
        await pool.fetch(
            sqlres.load("chat_bind_peer.sql"), job["session_id"], user_id,
        )
    channel = "bot" if use_bot else "direct"
    await _finish(pool, job["job_id"], True, None, msg.id, channel)


async def run_sender(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    sim = os.getenv("NEXUS_BRIDGE_SIM", "0") == "1"
    send = _send_sim if sim else _send_real
    try:
        account_ids = (
            await _sim_account_ids(pool) if sim else await _real_account_ids(pool)
        )
    except Exception as exc:
        logger.error("sender cannot resolve accounts: %s", exc)
        await stop.wait()
        return
    if not account_ids and not sim:
        logger.info("sender idle (no active gateway accounts yet)")
    logger.info(
        "sender loop started (mode=%s accounts=%d)",
        "sim" if sim else "real", len(account_ids),
    )
    while not stop.is_set():
        try:
            for account_id in account_ids:
                jobs = await pool.fetch(SQL_CLAIM, account_id)
                for job in jobs:
                    logger.info(
                        "outbox claimed job=%s contact=%s (%d chars)",
                        job["job_id"], job["contact_username"]
                        or job["contact_peer_id"], len(job["content"]),
                    )
                    try:
                        await send(pool, account_id, job)
                    except Exception:
                        # a crash must never kill the loop silently — the
                        # job goes back through the normal failure path
                        logger.exception("outbox job %s crashed", job["job_id"])
                        try:
                            await _finish(pool, job["job_id"], False,
                                          "sender crashed on this job", None)
                        except Exception:
                            logger.exception("finish after crash failed too")
            await _sleep(stop, POLL_S)
            # clients are owned by the shared hub; nothing to tear down here
        except Exception:
            logger.exception("sender loop cycle failed; continuing")
            await _sleep(stop, POLL_S)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
