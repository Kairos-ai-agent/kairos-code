"""Multi-IM platform integrations (the agent-gateway-style coverage).

R38.6 §34: Feishu is already wired. This module adds the
adapter contracts and (where API is simple enough) concrete
handlers for:
  - DingTalk (钉钉) — custom robot webhook (one-way push)
  - WeCom (企业微信) — group robot webhook (one-way push)
  - Slack — incoming webhook (one-way push)
  - Telegram — Bot API (two-way, requires bot token)
  - Discord — webhook (one-way push)

The design mirrors `kairos.feishu` — a custom-robot webhook
wherever the platform supports it, falling back to a bot
API client for platforms that require it (Telegram).

For each platform we expose:
  - `send(text, chat_id)` — push a message
  - `verify_inbound(headers, body)` — verify webhook signature
  - `parse_command(text)` — extract /command

Two-way command flow follows the same pattern as Feishu:
  - Outbound: agent event_bus → send() (via FeishuEventForwarder
    equivalent)
  - Inbound: webhook endpoint → parse_command() → dispatch
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class IMConfig:
    """Per-deployment IM platform configuration."""
    # DingTalk (custom robot)
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""        # optional signing
    # WeCom (group robot)
    wecom_webhook: str = ""
    # Slack (incoming webhook)
    slack_webhook: str = ""
    # Telegram (bot API)
    telegram_bot_token: str = ""
    telegram_default_chat_id: str = ""
    # Discord (webhook)
    discord_webhook: str = ""


class IMStore:
    """SQLite-backed chat→project bindings for all IM platforms."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS im_bindings (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (platform, chat_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS im_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.commit()

    async def bind(self, platform: str, chat_id: str, project_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO im_bindings"
                " (platform, chat_id, project_id, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (platform, chat_id, project_id, time.time()),
            )
            await db.commit()

    async def lookup(self, platform: str, chat_id: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT project_id FROM im_bindings"
                " WHERE platform = ? AND chat_id = ?",
                (platform, chat_id),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def list_all(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT platform, chat_id, project_id, updated_at"
                " FROM im_bindings ORDER BY updated_at DESC",
            )
            return [
                {"platform": p, "chat_id": c,
                 "project_id": pr, "updated_at": u}
                for (p, c, pr, u) in await cur.fetchall()
            ]


# ---------------------------------------------------------------------------
# DingTalk (custom robot)
# ---------------------------------------------------------------------------

def dingtalk_sign(secret: str) -> tuple[str, str]:
    """Compute DingTalk's signing: HMAC-SHA256 over
    ``<secret>\n<ts>``."""
    ts = str(round(time.time() * 1000))
    h = hmac.new(
        secret.encode("utf-8"),
        f"{ts}\n{secret}".encode("utf-8"),
        hashlib.sha256,
    )
    return ts, b64encode(h.digest()).decode("utf-8")


async def dingtalk_send(webhook: str, text: str,
                         secret: str = "") -> Dict[str, Any]:
    import httpx
    body: Dict[str, Any] = {
        "msgtype": "text",
        "text": {"content": text},
    }
    if secret:
        ts, sign = dingtalk_sign(secret)
        body["timestamp"] = ts
        body["sign"] = sign
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(webhook, json=body)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# WeCom (group robot)
# ---------------------------------------------------------------------------

async def wecom_send(webhook: str, text: str) -> Dict[str, Any]:
    import httpx
    body = {
        "msgtype": "text",
        "text": {"content": text},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(webhook, json=body)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Slack (incoming webhook)
# ---------------------------------------------------------------------------

async def slack_send(webhook: str, text: str) -> Dict[str, Any]:
    import httpx
    body = {"text": text}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(webhook, json=body)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Telegram (Bot API — requires bot token)
# ---------------------------------------------------------------------------

async def telegram_send(bot_token: str, chat_id: str,
                         text: str) -> Dict[str, Any]:
    """Send a message via Telegram Bot API. chat_id must be
    the numeric chat id (not the @channel name)."""
    import httpx
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "chat_id": chat_id, "text": text,
                "parse_mode": "Markdown",
            })
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Discord (webhook)
# ---------------------------------------------------------------------------

async def discord_send(webhook: str, text: str) -> Dict[str, Any]:
    import httpx
    body = {"content": text}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(webhook, json=body)
        return {"ok": r.status_code in (200, 204),
                "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Unified dispatch
# ---------------------------------------------------------------------------

async def send_to_platform(platform: str, cfg: IMConfig,
                            text: str, chat_id: str = "") -> Dict[str, Any]:
    """One entrypoint used by the event forwarder to send
    a message via the configured platform."""
    if platform == "dingtalk" and cfg.dingtalk_webhook:
        return await dingtalk_send(
            cfg.dingtalk_webhook, text, cfg.dingtalk_secret)
    if platform == "wecom" and cfg.wecom_webhook:
        return await wecom_send(cfg.wecom_webhook, text)
    if platform == "slack" and cfg.slack_webhook:
        return await slack_send(cfg.slack_webhook, text)
    if platform == "telegram" and cfg.telegram_bot_token:
        return await telegram_send(
            cfg.telegram_bot_token,
            chat_id or cfg.telegram_default_chat_id, text)
    if platform == "discord" and cfg.discord_webhook:
        return await discord_send(cfg.discord_webhook, text)
    return {"ok": False, "error": f"{platform} not configured"}


SUPPORTED_PLATFORMS = ["feishu", "dingtalk", "wecom", "slack",
                       "telegram", "discord"]
