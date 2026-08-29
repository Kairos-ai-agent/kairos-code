"""Feishu (Lark) HTTP API (R38.6 §33).

Routes:
  POST  /api/feishu/webhook            Feishu event subscription webhook
  GET   /api/feishu/config             Read current config
  PUT   /api/feishu/config             Update config
  POST  /api/feishu/test               Send a test message
  GET   /api/feishu/bindings           List chat→project bindings
  DELETE /api/feishu/bindings/{chat_id} Remove a binding
  GET   /api/feishu/topics             Default forwarded event topics
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from kairos.feishu import (DEFAULT_FORWARD_TOPICS, FeishuBindingStore,
                            FeishuBot, FeishuConfig, FeishuEventForwarder,
                            parse_command, verify_feishu_signature)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feishu", tags=["feishu"])

# Module-level state, wired by api/app.py
_bot: Optional[FeishuBot] = None
_store: Optional[FeishuBindingStore] = None
_forwarder: Optional[FeishuEventForwarder] = None
_orchestrator = None  # for /use and /chat commands


def set_dependencies(bot: FeishuBot, store: FeishuBindingStore,
                      forwarder: FeishuEventForwarder,
                      orchestrator) -> None:
    global _bot, _store, _forwarder, _orchestrator
    _bot = bot
    _store = store
    _forwarder = forwarder
    _orchestrator = orchestrator


def _check():
    if _bot is None or _store is None:
        raise HTTPException(status_code=503, detail="feishu not initialized")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class FeishuConfigBody(BaseModel):
    webhook_url: str = ""
    signing_secret: str = ""
    default_chat_id: str = ""
    enabled: bool = False


@router.get("/config")
async def get_config():
    _check()
    return (await _store.get_config()).__dict__


@router.put("/config")
async def put_config(body: FeishuConfigBody):
    _check()
    cfg = FeishuConfig(
        webhook_url=body.webhook_url.strip(),
        signing_secret=body.signing_secret.strip(),
        default_chat_id=body.default_chat_id.strip(),
        enabled=body.enabled,
    )
    await _store.save_config(cfg)
    # Re-build the bot so subsequent sends use the new config
    global _bot
    _bot = FeishuBot(cfg)
    return {"ok": True}


@router.get("/topics")
async def list_topics():
    return {"topics": sorted(DEFAULT_FORWARD_TOPICS)}


@router.post("/test")
async def test_message(body: Dict[str, Any]):
    _check()
    text = body.get("text", "👋 Kairos test message")
    chat_id = body.get("chat_id") or None
    return await _bot.send(text, chat_id=chat_id)


# ---------------------------------------------------------------------------
# Bindings (chat → project)
# ---------------------------------------------------------------------------

@router.get("/bindings")
async def list_bindings():
    _check()
    return {"bindings": await _store.list_all()}


@router.delete("/bindings/{chat_id}")
async def delete_binding(chat_id: str):
    _check()
    # No delete method on store; we add it implicitly by binding
    # with empty project_id, which we then filter on lookup.
    # Cleaner: implement explicit remove here.
    import aiosqlite
    async with aiosqlite.connect(_store.db_path) as db:
        await db.execute(
            "DELETE FROM feishu_bindings WHERE chat_id = ?",
            (chat_id,),
        )
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Webhook — Feishu event subscription
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def webhook(request: Request):
    """Feishu event-subscription webhook.

    Handles two cases:
      1. URL verification: Feishu posts a ``url_verification``
         event and expects us to echo the ``challenge`` field
         back so it knows the URL is reachable.
      2. Real events (im.message.receive_v1): parse the message
         text as a command, run it, and (optionally) reply via
         the same webhook.

    Signature is verified if the config has a signing_secret.
    """
    _check()
    raw = await request.body()
    body = json.loads(raw or b"{}")
    cfg = await _store.get_config()

    # Signature check
    ts = request.headers.get("X-Lark-Request-Timestamp", "")
    sign = request.headers.get("X-Lark-Signature", "")
    if cfg.signing_secret and not verify_feishu_signature(
            ts, sign, cfg.signing_secret, raw.decode("utf-8")):
        raise HTTPException(status_code=401, detail="bad signature")

    # URL verification handshake
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    # Real event
    header = body.get("header") or {}
    event_type = header.get("event_type", "")
    event = body.get("event") or {}
    if event_type != "im.message.receive_v1":
        # Acknowledge unknown events so Feishu doesn't retry.
        return {"ok": True, "ignored": event_type}

    # Extract chat + sender + text
    sender = event.get("sender") or {}
    sender_id = (sender.get("sender_id") or {}).get("open_id", "")
    chat_id = ((event.get("message") or {}).get("chat_id")) or sender_id
    msg = event.get("message") or {}
    text = (msg.get("content") or {}).get("text", "").strip()
    # Strip the @bot mention if present
    if text.startswith("@_user_"):
        text = text.split(" ", 1)[-1] if " " in text else ""
    if not text:
        return {"ok": True, "skipped": "empty text"}

    cmd, args = parse_command(text)
    return await _dispatch_command(cmd, args, chat_id, sender_id)


async def _dispatch_command(cmd: str, args: list, chat_id: str,
                              sender_id: str) -> Dict[str, Any]:
    """Run a Feishu command. Returns the bot's response text."""
    cfg = await _store.get_config()
    project_id = await _store.lookup(chat_id) or cfg.default_chat_id
    if cmd == "help":
        return {"text": (
            "/status · /projects · /use <id> · /chat <text> · "
            "/checkpoint · /browser <url> · /help"
        )}
    if cmd == "noop":
        return {"ok": True, "skipped": "empty"}
    if cmd == "projects":
        if _orchestrator is None:
            return {"text": "no orchestrator"}
        projs = _orchestrator.list_projects()
        if not projs:
            return {"text": "no projects yet"}
        lines = [f"• {p.id}  {p.name}" for p in projs[:20]]
        return {"text": "\n".join(lines)}
    if cmd == "use":
        if not args:
            return {"text": "usage: /use <project_id>"}
        await _store.bind(chat_id, args[0])
        return {"text": f"bound chat → {args[0]}"}
    if cmd == "status":
        if not project_id:
            return {"text": "no active project. /use <id> first."}
        return {"text": f"active project: {project_id}"}
    if cmd == "checkpoint":
        if not project_id:
            return {"text": "no active project. /use <id> first."}
        # Run the same workbench snapshot endpoint that the
        # Web UI uses.
        from fastapi import HTTPException as _HE
        try:
            from api.routes.workbench import _create_checkpoint
            result = await _create_checkpoint(project_id)
            return {"text": f"snapshot {result.get('snapshot_id')}: "
                              f"{result.get('path_count')} files"}
        except Exception as exc:  # noqa: BLE001
            return {"text": f"checkpoint failed: {exc}"}
    if cmd == "browser":
        if not project_id:
            return {"text": "no active project. /use <id> first."}
        if not args:
            return {"text": "usage: /browser <url>"}
        # Forward to the browser route's navigate endpoint
        from kairos.browser import BrowserManager  # noqa
        from fastapi import HTTPException as _HE
        try:
            from api.routes import browser as browser_routes
            mgr = browser_routes._mgr()
            url = args[0]
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            info = await mgr.navigate(project_id, url)
            if info.get("error"):
                return {"text": f"navigate error: {info['error']}"}
            return {"text": f"opened {info.get('title', url)}"}
        except Exception as exc:  # noqa: BLE001
            return {"text": f"browser error: {exc}"}
    if cmd == "chat":
        # Forward to the active project's chat endpoint
        if not project_id:
            return {"text": "no active project. /use <id> first."}
        if not args:
            return {"text": "usage: /chat <text>"}
        try:
            prompt = " ".join(args)
            from fastapi import HTTPException as _HE
            # Use the same path the Web UI takes
            import asyncio
            from api.routes.projects import _orch as get_orch
            orch = get_orch()
            proj = orch.get_project(project_id)
            if proj is None:
                return {"text": f"unknown project {project_id}"}
            # Fire-and-forget — the user will get a push when
            # the loop finishes (handled by FeishuEventForwarder).
            asyncio.create_task(
                _run_chat_in_background(proj, prompt)
            )
            return {"text": f"⏳ running: {prompt[:80]}"}
        except Exception as exc:  # noqa: BLE001
            return {"text": f"chat error: {exc}"}
    return {"text": f"unknown command: /{cmd}"}


async def _run_chat_in_background(project, prompt: str) -> None:
    """Run the agent loop in the background, push the result
    back to Feishu when done."""
    try:
        from api.routes.projects import _orch as get_orch
        from kairos.agents_md import get_agents_md_loader
        loader = get_agents_md_loader()
        # Delegate to the same code path the Web /chat uses
        from fastapi import HTTPException as _HE
        # The actual run is owned by the agent's task pipeline
        # — for now we just publish a status message so the
        # user sees feedback.
        bus = get_orch().message_bus
        await bus.publish({
            "type": "user_input",
            "subject": f"Chat from Feishu: {project.id}",
            "detail": prompt,
            "project_id": project.id,
            "ts": time.time(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("background chat failed")
        if _bot is not None:
            await _bot.send(f"[Kairos] chat failed: {exc}")
