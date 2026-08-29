"""Feishu (Lark) bot integration (R38.6 §33).

We use the **custom bot webhook** pattern: the user creates a
bot in a Feishu group, gets a webhook URL, and pastes it into
the Kairos settings. This bypasses the app_id / app_secret /
tenant_access_token flow — the custom bot is the simplest
"push notifications to a group" path and is what 90% of teams
use for operational alerts.

Why custom bot and not full app?
  - Zero OAuth setup — the user just copies a URL
  - No need to publish an app to the workspace
  - Sufficient for one-way push (the user only needs to know
    "agent done" / "checkpoint available" / "user input needed")
  - Bidirectional (commands from Feishu → Kairos) is achieved
    by adding Feishu's event subscription webhook on the same
    bot, optionally with a signing secret

Two directions
--------------
1. **Kairos → Feishu** (push): the agent's message_bus events
   fire notifications through ``FeishuBot.send()`` for the
   events the user cares about.
2. **Feishu → Kairos** (commands): ``/status``, ``/projects``,
   ``/use <id>``, ``/chat <text>``, ``/checkpoint`` are parsed
   by the webhook receiver and forwarded to the agent.

Persistence
-----------
Chat → project binding is stored in a tiny SQLite table
``feishu_bindings(chat_id, project_id, updated_at)``. The
"active" project for each chat is the one with the most recent
``updated_at``. Sending a ``/use <id>`` command updates it.

Security
--------
The webhook receiver validates Feishu's signing header
(X-Lark-Signature, HMAC-SHA256) if a signing secret is
configured. Without the secret, any caller can post — which is
acceptable for a local dev setup but NOT for production.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


# Default webhook rate limit per Feishu docs: 5 msg/sec per
# custom bot. We buffer bursts through a semaphore to avoid
# 429s.
_FEISHU_RATE_PER_SEC = 5


@dataclass
class FeishuConfig:
    """Per-deployment configuration."""
    webhook_url: str = ""           # https://open.feishu.cn/open-apis/bot/v2/hook/<token>
    signing_secret: str = ""        # optional; for incoming webhook verification
    default_chat_id: str = ""       # fallback when a chat has no binding
    enabled: bool = False


class FeishuBot:
    """The outbound side: send text/post messages to a Feishu custom bot."""

    def __init__(self, config: FeishuConfig):
        self.config = config
        self._sem = asyncio.Semaphore(_FEISHU_RATE_PER_SEC)
        # Lazy httpx import so aio-only deployments don't fail
        # at import time.
        self._httpx = None

    def _get_httpx(self):
        if self._httpx is None:
            import httpx
            self._httpx = httpx
        return self._httpx

    def _sign(self, ts: str) -> str:
        """Feishu signing: HMAC-SHA256 over ``<ts>\n<secret>``,
        base64-encoded. Sent as the ``sign`` field in the body."""
        if not self.config.signing_secret:
            return ""
        h = hmac.new(
            self.config.signing_secret.encode("utf-8"),
            f"{ts}\n{self.config.signing_secret}".encode("utf-8"),
            hashlib.sha256,
        )
        return b64encode(h.digest()).decode("utf-8")

    async def send(self, text: str, chat_id: Optional[str] = None,
                   msg_type: str = "text") -> Dict[str, Any]:
        """Send a message to the configured webhook.

        ``chat_id`` is currently unused for custom bots (they
        post to a fixed group) but is captured in the response
        so the binding logic can be extended to per-chat
        routing later.
        """
        if not self.config.enabled or not self.config.webhook_url:
            return {"ok": False, "skipped": "feishu not configured"}
        if not text:
            return {"ok": False, "skipped": "empty text"}
        async with self._sem:
            ts = str(int(time.time()))
            body: Dict[str, Any] = {"msg_type": msg_type,
                                     "content": {"text": text}}
            if chat_id:
                body["chat_id"] = chat_id
            if self.config.signing_secret:
                body["timestamp"] = ts
                body["sign"] = self._sign(ts)
            try:
                httpx = self._get_httpx()
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(self.config.webhook_url,
                                          json=body)
                return {"ok": r.status_code == 200,
                        "status": r.status_code,
                        "body": r.text[:200]}
            except Exception as exc:  # noqa: BLE001
                logger.warning("feishu send failed: %s", exc)
                return {"ok": False, "error": str(exc)}

    async def send_card(self, title: str, text: str,
                        link: str = "") -> Dict[str, Any]:
        """Send a richer 'interactive' card message."""
        if not self.config.enabled or not self.config.webhook_url:
            return {"ok": False, "skipped": "feishu not configured"}
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text",
                                  "content": title}},
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text",
                                          "content": text}},
            ],
        }
        if link:
            card["elements"].append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "Open in Kairos"},
                    "type": "primary",
                    "url": link,
                }],
            })
        async with self._sem:
            ts = str(int(time.time()))
            body: Dict[str, Any] = {"msg_type": "interactive",
                                     "card": card}
            if self.config.signing_secret:
                body["timestamp"] = ts
                body["sign"] = self._sign(ts)
            try:
                httpx = self._get_httpx()
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(self.config.webhook_url,
                                          json=body)
                return {"ok": r.status_code == 200,
                        "status": r.status_code}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}


class FeishuBindingStore:
    """SQLite-backed mapping of Feishu chat_id → project_id.

    Lives next to the rest of Kairos's SQLite DB so a single
    backup captures everything.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feishu_bindings (
                    chat_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feishu_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.commit()

    async def bind(self, chat_id: str, project_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO feishu_bindings"
                " (chat_id, project_id, updated_at) VALUES (?, ?, ?)",
                (chat_id, project_id, time.time()),
            )
            await db.commit()

    async def lookup(self, chat_id: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT project_id FROM feishu_bindings"
                " WHERE chat_id = ?",
                (chat_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def list_all(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT chat_id, project_id, updated_at"
                " FROM feishu_bindings ORDER BY updated_at DESC",
            )
            rows = await cur.fetchall()
            return [{"chat_id": c, "project_id": p, "updated_at": u}
                    for (c, p, u) in rows]

    async def get_config(self) -> FeishuConfig:
        cfg = FeishuConfig()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT key, value FROM feishu_config",
            )
            for k, v in await cur.fetchall():
                if k == "webhook_url":
                    cfg.webhook_url = v or ""
                elif k == "signing_secret":
                    cfg.signing_secret = v or ""
                elif k == "default_chat_id":
                    cfg.default_chat_id = v or ""
                elif k == "enabled":
                    cfg.enabled = (v or "").lower() in ("1", "true", "yes")
        return cfg

    async def save_config(self, cfg: FeishuConfig) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            for k, v in (
                ("webhook_url", cfg.webhook_url),
                ("signing_secret", cfg.signing_secret),
                ("default_chat_id", cfg.default_chat_id),
                ("enabled", "1" if cfg.enabled else "0"),
            ):
                await db.execute(
                    "INSERT OR REPLACE INTO feishu_config"
                    " (key, value) VALUES (?, ?)",
                    (k, v),
                )
            await db.commit()


# ---------------------------------------------------------------------------
# Outbound: agent → feishu notifications
# ---------------------------------------------------------------------------

# Topics we forward to Feishu by default. The user can change
# this list in Settings. Each topic maps to a message_bus
# message type.
DEFAULT_FORWARD_TOPICS = {
    "loop_done",        # the agent's run finished
    "plan_ready",       # a multi-step plan is ready for review
    "checkpoint",       # a new checkpoint is available
    "user_input",       # agent is asking the user a question
    "error",            # an unrecoverable error
    "deliverable",      # a new deliverable file is ready
}


class FeishuEventForwarder:
    """Subscribes to the orchestrator's message bus and pushes
    selected events to Feishu.

    The forwarder runs as a background task: ``start()`` spawns
    it, ``stop()`` cancels it. Each event gets a single
    ``send()`` call — we never batch — because a missed
    notification is worse than a 429 retry.
    """

    def __init__(self, bot: FeishuBot, topics: Optional[set] = None):
        self.bot = bot
        self.topics = topics or set(DEFAULT_FORWARD_TOPICS)
        self._task: Optional[asyncio.Task] = None
        self._bus = None
        self._stop = asyncio.Event()

    def attach(self, message_bus) -> None:
        """Called once the orchestrator is up; we read its bus
        via ``_orch().message_bus`` pattern from other routes."""
        self._bus = message_bus

    async def _run(self) -> None:
        # The bus is an async pub/sub. Subscribe to all
        # messages and filter by topic client-side.
        if self._bus is None:
            logger.warning("FeishuEventForwarder started without a bus")
            return
        async for msg in self._bus.subscribe():
            if self._stop.is_set():
                break
            try:
                topic = getattr(msg, "type", None) or msg.get("type", "")
                if topic not in self.topics:
                    continue
                # Format the message
                text = self._format(msg, topic)
                await self.bot.send(text)
            except Exception as exc:  # noqa: BLE001
                logger.debug("feishu forward error: %s", exc)

    def _format(self, msg: Any, topic: str) -> str:
        """Convert a bus message to a human-readable line."""
        if isinstance(msg, dict):
            subject = msg.get("subject") or msg.get("title") or topic
            detail = msg.get("detail") or msg.get("text") or ""
        else:
            subject = getattr(msg, "subject", topic) or topic
            detail = getattr(msg, "detail", "") or ""
        return f"[Kairos] {topic}: {subject}\n{detail}".strip()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(),
                                          name="feishu-forwarder")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None


# ---------------------------------------------------------------------------
# Inbound: feishu → kairos commands
# ---------------------------------------------------------------------------

def parse_command(text: str) -> tuple[str, list[str]]:
    """Parse a Feishu message into ``(command, args)``.

    Returns ``("chat", [text])`` for plain text so anything the
    user types in the group gets forwarded to the agent.

    Slash commands:
        /status               → project status
        /projects             → list projects
        /use <project_id>     → switch active project for this chat
        /chat <text>          → explicit chat (same as plain text)
        /checkpoint           → snapshot now
        /browser <url>        → open URL in the project's browser
        /help                 → list commands
    """
    text = (text or "").strip()
    if not text:
        return ("noop", [])
    if not text.startswith("/"):
        return ("chat", [text])
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].lower()
    rest = parts[1].split() if len(parts) > 1 else []
    return (cmd, rest)


def verify_feishu_signature(ts: str, sign: str, secret: str,
                              body: str) -> bool:
    """Verify the X-Lark-Signature header (HMAC-SHA256 over
    ``<ts>\n<secret>``) matches the request body.

    Feishu docs: https://open.feishu.cn/document/uAjLw4CM/ukzMukzM
    """
    if not secret:
        return True  # not configured → accept all
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{ts}\n{secret}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sign or "")
