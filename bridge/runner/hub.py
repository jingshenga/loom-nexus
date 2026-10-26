"""Shared TelegramClient hub: ONE live client per session file.

Telethon SQLite sessions must not be opened by several concurrent
clients ("database is locked"); a single client is itself safe for
concurrent async calls, so every bridge task (intake, dialogs,
presence, sender) borrows its client from here.
"""
import asyncio
import logging
import os

logger = logging.getLogger("nexus-bridge.hub")

_lock = asyncio.Lock()
_clients = {}


def _env_creds():
    api_id = int(os.getenv("NEXUS_BRIDGE_API_ID", "0"))
    api_hash = os.getenv("NEXUS_BRIDGE_API_HASH", "")
    return api_id, api_hash


async def get_client(session_name: str):
    """Return a connected client for the session (created on first use).
    Returns None when the account is unauthorized."""
    from telethon import TelegramClient

    async with _lock:
        client = _clients.get(session_name)
        if client is not None and client.is_connected():
            return client
        api_id, api_hash = _env_creds()
        client = TelegramClient(
            os.path.join(
                os.getenv("NEXUS_BRIDGE_SESSION_DIR", "/app/data/sessions"),
                session_name,
            ),
            api_id, api_hash,
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        _clients[session_name] = client
        return client


async def disconnect_client(session_name: str) -> None:
    """Drop one client (account taken offline via the admin page)."""
    async with _lock:
        client = _clients.pop(session_name, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


async def disconnect_all() -> None:
    async with _lock:
        for client in _clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        _clients.clear()
