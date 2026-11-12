"""Shared LLM configuration and OpenAI-compatible chat access.

Config resolution order: app_settings table (set via the admin UI,
takes effect immediately) -> environment variables. Endpoint URLs are
validated before any request (scheme allowlist, resolved-IP policy
blocking link-local/unspecified and, unless opted in, loopback/private
ranges; redirects refused).
"""
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import HTTPException

from app import sqlres

SQL_GET = sqlres.load("settings_get.sql")
SQL_PUT = sqlres.load("settings_upsert.sql")

SETTING_KEYS = ("llm_base_url", "llm_api_key", "llm_model", "llm_allow_private",
                "autopublish_threshold", "autopublish_enabled")


async def load_auto_publish(pool) -> dict:
    """Auto-publish policy from app_settings (default: on, >= 0.8)."""
    from app import sqlres as _sqlres

    sql = _sqlres.load("settings_get.sql")
    sql_num = _sqlres.load("settings_get_num.sql")
    rows = await pool.fetch(sql, "autopublish_enabled")
    enabled_raw = rows[0]["value"] if rows else None
    thresh = await pool.fetchval(sql_num, "autopublish_threshold", 0.7)
    return {
        "enabled": enabled_raw is None or enabled_raw == "1",
        "threshold": float(thresh),
    }


async def load_config(pool) -> dict:
    """DB-first, env-fallback LLM config."""
    stored = {}
    for key in SETTING_KEYS:
        rows = await pool.fetch(SQL_GET, key)
        stored[key] = rows[0]["value"] if rows and rows[0]["value"] else None
    return {
        "base_url": stored["llm_base_url"] or os.getenv("NEXUS_LLM_BASE_URL", ""),
        "api_key": stored["llm_api_key"] or os.getenv("NEXUS_LLM_API_KEY", ""),
        "model": stored["llm_model"] or os.getenv("NEXUS_LLM_MODEL", ""),
        "allow_private": (
            stored["llm_allow_private"]
            if stored["llm_allow_private"] is not None
            else os.getenv("NEXUS_LLM_ALLOW_PRIVATE_HOSTS", "0")
        ) == "1",
        "source": "db" if stored["llm_base_url"] else "env",
    }


async def save_config(pool, base_url: str, api_key, model: str,
                      allow_private: bool) -> None:
    """Persist settings; api_key=None keeps the stored value."""
    await pool.fetch(SQL_PUT, "llm_base_url", base_url or "")
    if api_key is not None and api_key != "":
        await pool.fetch(SQL_PUT, "llm_api_key", api_key)
    await pool.fetch(SQL_PUT, "llm_model", model or "")
    await pool.fetch(SQL_PUT, "llm_allow_private", "1" if allow_private else "0")


def checked_endpoint(base_url: str, allow_private: bool) -> str:
    """Validate the LLM endpoint before any request is made."""
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="base url 必须是 http(s)")
    try:
        infos = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise HTTPException(status_code=502, detail=f"域名解析失败: {exc}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            raise HTTPException(
                status_code=400, detail=f"端点地址 {ip} 不在允许范围",
            )
        if not allow_private and (ip.is_loopback or ip.is_private or ip.is_reserved):
            raise HTTPException(
                status_code=400,
                detail=f"端点 {ip} 是私网/环回地址；如确需局域网大模型请勾选『允许私网端点』",
            )
    return base_url.rstrip("/") + "/chat/completions"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, "redirect refused", headers, fp)


def chat(cfg: dict, messages: list, timeout: int = 60) -> str:
    """One synchronous chat completion; returns the message content."""
    endpoint = checked_endpoint(cfg["base_url"], cfg["allow_private"])
    payload = json.dumps({
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        endpoint, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]
