"""Protocol client wiring (long-lived gateway connections, Telethon).

Session files live under data/sessions/<name>.session (bind-mounted).
First login: accounts admin page (login_requests workflow) or
`python -m runner.login <name>`. Afterwards connections are non-
interactive. Media binaries are downloaded at intake time; type-B links
are fetched and merged inline.

Sources bound or unbound at runtime (admin UI) are picked up by the
reconcile loop within a minute — no restart needed. The same loop also
tracks gateway accounts going online/offline (accounts admin page) and
attaches one inbound-DM handler per account that routes contact replies
into open proxied chat sessions.
"""
import asyncio
import logging
import os

from runner import accounts, duty, hub, sqlres
from runner.backfill import save_media
from runner.listener import LINK_PATTERN, expand_embedded_links, order_media

logger = logging.getLogger("nexus-bridge.protocol")

SESSION_DIR = os.getenv("NEXUS_BRIDGE_SESSION_DIR", "/app/data/sessions")

RECONCILE_S = 60.0


def _make_link_fetcher(client):
    """Resolve one internal link target (type B incremental fetch)."""
    async def fetch_target(chat_ref: str, msg_id: int):
        try:
            entity = int("-100" + chat_ref) if chat_ref.isdigit() else chat_ref
            target = await client.get_messages(entity, ids=msg_id)
            if target and target.raw_text:
                return target.raw_text
        except Exception as exc:
            logger.warning("link target fetch failed (%s/%s): %s", chat_ref, msg_id, exc)
        return None
    return fetch_target


def _make_handler(pipeline, source_id, client):
    async def handler(event):
        msg = event.message
        media = await save_media(msg, source_id)
        text = msg.raw_text or ""
        if LINK_PATTERN.search(text):
            text, fetched = await expand_embedded_links(text, _make_link_fetcher(client))
            logger.info("type-B message %s merged %d link target(s)", msg.id, fetched)
        await pipeline.accept(
            source_id=source_id,
            platform_msg_id=msg.id,
            sender_peer_id=msg.sender_id,
            grouped_id=msg.grouped_id,
            text=text,
            media=media,
            posted_at=msg.date,
        )
    return handler


def _make_album_handler(pipeline, source_id, client):
    async def handler(event):
        messages = sorted(event.messages, key=lambda msg: msg.id)
        if not messages:
            return
        caption = next(
            (msg for msg in messages if (msg.raw_text or "").strip()),
            messages[0],
        )
        parts = await asyncio.gather(
            *(save_media(msg, source_id) for msg in messages)
        )
        media = order_media([entry for part in parts for entry in part])
        text = caption.raw_text or ""
        if LINK_PATTERN.search(text):
            text, fetched = await expand_embedded_links(text, _make_link_fetcher(client))
            logger.info("type-B album %s merged %d link target(s)", event.grouped_id, fetched)
        await pipeline.accept(
            source_id=source_id,
            platform_msg_id=caption.id,
            sender_peer_id=caption.sender_id,
            grouped_id=event.grouped_id,
            text=text,
            media=media,
            posted_at=caption.date,
        )
    return handler


def _make_dm_handler(pool, account_id):
    """Route inbound private replies (text or photo) into the open
    proxied session; photos are saved under media/chat/."""
    sql_incoming = sqlres.load("chat_incoming.sql")
    import os

    chat_dir = os.path.join(
        os.getenv("NEXUS_MEDIA_DIR", "/app/data/media"), "chat",
    )

    async def handler(event):
        # group posts also match NewMessage(incoming=True); only 1:1
        # private messages belong to proxied chats
        if not getattr(event, "is_private", False):
            return
        msg = event.message
        text = msg.raw_text or ""
        media_kind = None
        media_path = None
        if getattr(msg, "photo", None):
            try:
                os.makedirs(chat_dir, exist_ok=True)
                saved = await msg.download_media(file=chat_dir)
                if saved:
                    media_kind = "photo"
                    media_path = os.path.relpath(
                        str(saved),
                        os.getenv("NEXUS_MEDIA_DIR", "/app/data/media"),
                    )
            except Exception as exc:
                logger.warning("dm photo download failed: %s", exc)
        if not text and not media_path:
            return
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
        peer_id = getattr(sender, "id", None) or msg.sender_id
        username = getattr(sender, "username", None)
        rows = await pool.fetch(
            sql_incoming, account_id, peer_id, username, msg.id, text,
            media_kind, media_path,
        )
        if rows[0]["message_id"] is not None:
            logger.info(
                "dm inbound stored (from=@%s msg=%s media=%s)",
                username or peer_id, msg.id, media_kind or "text",
            )
    return handler


async def run_protocol_intake(pipeline, pool, stop) -> None:
    from telethon import events

    handlers = {}            # routing key -> (client, [(handler, event), ...])
    session_by_account = {}  # account_id -> session_name (clients online)

    async def disconnect_account(account_id) -> None:
        for key in [k for k in handlers if k[0] == account_id or k == ("dm", account_id)]:
            client, registrations = handlers.pop(key)
            for handler, event in registrations:
                try:
                    client.remove_event_handler(handler, event)
                except Exception:
                    pass
        session_name = session_by_account.pop(account_id, None)
        if session_name:
            await hub.disconnect_client(session_name)
            logger.info("account %s offline; client disconnected", account_id)

    async def reconcile():
        """Sync online clients, bound-source handlers and DM handlers."""
        client_by_account = {}
        account_rows = await accounts.load_active_accounts(pool)
        for acc in account_rows:
            session_name = str(acc["session_file"]).removesuffix(".session")
            client = await hub.get_client(session_name)
            if client is None:
                if session_by_account.get(acc["id"]) == session_name:
                    await disconnect_account(acc["id"])
                continue
            client_by_account[acc["id"]] = client
            session_by_account[acc["id"]] = session_name

        # accounts that went inactive since last cycle
        for account_id in list(session_by_account):
            if account_id not in client_by_account:
                await disconnect_account(account_id)

        if not client_by_account:
            logger.warning("no active gateway accounts; nothing to listen")
            return

        wanted = {}
        for account_id in client_by_account:
            rows = await pool.fetch(
                sqlres.load("sources_for_account.sql"), account_id,
            )
            for row in rows:
                wanted[(account_id, "source", row["peer_id"])] = {
                    "kind": "source", "source_id": row["id"],
                    "peer_id": row["peer_id"], "title": row["title"],
                }
            wanted[("dm", account_id)] = None
            duty_rows = await pool.fetch(
                sqlres.load("duty_rules_for_account.sql"), account_id,
            )
            for row in duty_rows:
                meta = dict(row)
                config = duty.decode_config(row["config_json"])
                if not config.get("sender_peer_id") or not config.get("match_prefix"):
                    logger.warning("invalid duty rule skipped: %s", row["rule_id"])
                    continue
                wanted[(account_id, "duty-new", row["rule_id"], row["source_id"])] = meta
                if config.get("update_mode") == "edited_snapshot":
                    wanted[(account_id, "duty-edit", row["rule_id"], row["source_id"])] = meta

        for key in [k for k in handlers if k not in wanted]:
            client, registrations = handlers.pop(key)
            for handler, event in registrations:
                try:
                    client.remove_event_handler(handler, event)
                except Exception:
                    pass
            logger.info("stopped listening: account=%s peer=%s", key[0], key[1])

        for key, meta in wanted.items():
            if key in handlers:
                continue
            if key[0] == "dm":
                client = client_by_account[key[1]]
                event = events.NewMessage(incoming=True)
                handler = _make_dm_handler(pool, key[1])
                client.add_event_handler(handler, event)
                handlers[key] = (client, [(handler, event)])
                logger.info("dm router attached (account=%s)", key[1])
                continue
            account_id = key[0]
            client = client_by_account[account_id]
            if meta.get("kind") == "source":
                standalone_event = events.NewMessage(
                    chats=meta["peer_id"],
                    func=lambda event: event.message.grouped_id is None,
                )
                standalone_handler = _make_handler(pipeline, meta["source_id"], client)
                client.add_event_handler(standalone_handler, standalone_event)
                album_event = events.Album(chats=meta["peer_id"])
                album_handler = _make_album_handler(pipeline, meta["source_id"], client)
                client.add_event_handler(album_handler, album_event)
                handlers[key] = (
                    client,
                    [(standalone_handler, standalone_event), (album_handler, album_event)],
                )
                logger.info(
                    "listening: %s (peer=%s account=%s)",
                    meta["title"], meta["peer_id"], account_id,
                )
                continue
            config = duty.decode_config(meta["config_json"])
            event_cls = events.MessageEdited if key[1] == "duty-edit" else events.NewMessage
            event = event_cls(
                chats=meta["peer_id"],
                from_users=int(config["sender_peer_id"]),
            )
            handler = duty.make_handler(pool, client, meta)
            client.add_event_handler(handler, event)
            handlers[key] = (client, [(handler, event)])
            logger.info(
                "duty listening: %s (%s peer=%s account=%s)",
                meta["rule_name"], key[1], meta["peer_id"], account_id,
            )
            if key[1] == "duty-new":
                await duty.recover_latest(pool, client, meta)

    try:
        while not stop.is_set():
            await reconcile()
            try:
                await asyncio.wait_for(stop.wait(), timeout=RECONCILE_S)
                break
            except TimeoutError:
                pass
    finally:
        for _client, registrations in handlers.values():
            for handler, event in registrations:
                try:
                    _client.remove_event_handler(handler, event)
                except Exception:
                    pass
