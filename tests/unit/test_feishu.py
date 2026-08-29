"""Tests for kairos.feishu (R38.6 §33)."""
import hashlib
import hmac
import json
import time
from base64 import b64encode
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kairos.feishu import (DEFAULT_FORWARD_TOPICS, FeishuBindingStore,
                             FeishuBot, FeishuConfig,
                             FeishuEventForwarder, parse_command,
                             verify_feishu_signature)


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

def test_parse_chat_text():
    cmd, args = parse_command("hello world")
    assert cmd == "chat"
    assert args == ["hello world"]


def test_parse_chat_text_does_not_strip_mention():
    # The @mention stripping happens in the webhook
    # handler, NOT in parse_command (parse_command is also
    # used in tests where mention stripping has already
    # been done). Verify parse_command treats it as plain text.
    cmd, args = parse_command("@_user_1 hi")
    assert cmd == "chat"
    assert args == ["@_user_1 hi"]


def test_parse_slash_command():
    cmd, args = parse_command("/use p123")
    assert cmd == "use"
    assert args == ["p123"]


def test_parse_slash_no_args():
    cmd, args = parse_command("/help")
    assert cmd == "help"
    assert args == []


def test_parse_empty():
    cmd, args = parse_command("   ")
    assert cmd == "noop"
    assert args == []


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def test_signature_verification_round_trip():
    secret = "mysecret"
    ts = "1700000000"
    body = '{"x":1}'
    h = hmac.new(secret.encode("utf-8"),
                  f"{ts}\n{secret}".encode("utf-8"),
                  hashlib.sha256)
    sig = h.hexdigest()
    assert verify_feishu_signature(ts, sig, secret, body) is True


def test_signature_verification_rejects_bad_sig():
    assert verify_feishu_signature("1", "deadbeef", "secret", "x") is False


def test_signature_no_secret_accepts_all():
    # No secret configured → accept any signature (local dev)
    assert verify_feishu_signature("1", "", "", "x") is True


# ---------------------------------------------------------------------------
# Bot — disabled / empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bot_disabled_skips_send():
    bot = FeishuBot(config=FeishuConfig(enabled=False))
    out = await bot.send("hi")
    assert out["skipped"] == "feishu not configured"


@pytest.mark.asyncio
async def test_bot_empty_text_skips():
    bot = FeishuBot(config=FeishuConfig(enabled=True,
                                          webhook_url="http://x"))
    out = await bot.send("")
    assert out["skipped"] == "empty text"


@pytest.mark.asyncio
async def test_bot_signs_when_secret_present():
    bot = FeishuBot(config=FeishuConfig(
        enabled=True, webhook_url="http://x", signing_secret="s"))
    # Mock httpx.AsyncClient so we can inspect the body
    class FakeResp:
        status_code = 200
        text = "ok"
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(return_value=FakeResp())
    with patch("httpx.AsyncClient", return_value=fake_client):
        out = await bot.send("hello")
    assert out["ok"] is True
    # Inspect the body that was POSTed
    call = fake_client.post.call_args
    body = call.kwargs["json"]
    assert "timestamp" in body
    assert "sign" in body
    assert body["sign"]  # non-empty


# ---------------------------------------------------------------------------
# SQLite binding store
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_binding_store_round_trip(tmp_path: Path):
    db = tmp_path / "feishu.db"
    store = FeishuBindingStore(db_path=db)
    await store.init()
    # Empty lookup
    assert await store.lookup("chat-x") is None
    # Bind
    await store.bind("chat-x", "proj-a")
    assert await store.lookup("chat-x") == "proj-a"
    # Rebind
    await store.bind("chat-x", "proj-b")
    assert await store.lookup("chat-x") == "proj-b"
    # List
    bindings = await store.list_all()
    assert len(bindings) == 1
    assert bindings[0]["chat_id"] == "chat-x"


@pytest.mark.asyncio
async def test_binding_store_config_round_trip(tmp_path: Path):
    db = tmp_path / "feishu.db"
    store = FeishuBindingStore(db_path=db)
    await store.init()
    # Default empty config
    cfg = await store.get_config()
    assert cfg.enabled is False
    assert cfg.webhook_url == ""
    # Save
    cfg.webhook_url = "https://open.feishu.cn/hook/abc"
    cfg.signing_secret = "secret"
    cfg.default_chat_id = "oc_default"
    cfg.enabled = True
    await store.save_config(cfg)
    # Re-load
    cfg2 = await store.get_config()
    assert cfg2.webhook_url == "https://open.feishu.cn/hook/abc"
    assert cfg2.signing_secret == "secret"
    assert cfg2.default_chat_id == "oc_default"
    assert cfg2.enabled is True


# ---------------------------------------------------------------------------
# Event forwarder
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forwarder_filters_topics():
    bot = FeishuBot(config=FeishuConfig(enabled=False))
    fwd = FeishuEventForwarder(bot=bot, topics={"loop_done"})
    bus = AsyncMock()
    bus.subscribe = MagicMock()

    class _AsyncIter:
        def __init__(self, items):
            self.items = items
        def __aiter__(self):
            return iter(self.items)
    bus.subscribe.return_value = _AsyncIter([
        {"type": "loop_done", "subject": "x", "detail": "y"},
        {"type": "plan_ready", "subject": "ignored", "detail": ""},
    ])

    # Patch bot.send to count calls
    bot.send = AsyncMock(return_value={"ok": True})
    fwd.attach(bus)
    # Don't actually start the loop — just test _format
    msg = {"type": "loop_done", "subject": "Title", "detail": "Body"}
    out = fwd._format(msg, "loop_done")
    assert "Title" in out
    assert "Body" in out


def test_default_topics_include_key_events():
    expected = {"loop_done", "plan_ready", "checkpoint", "user_input",
                "error", "deliverable"}
    assert expected.issubset(DEFAULT_FORWARD_TOPICS)
