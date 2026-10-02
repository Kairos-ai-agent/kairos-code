"""企业微信「自建应用」通道测试 (R38.6 §35)。

全部离线：不连企业微信，也不依赖真实账号。HTTP 层用 FastAPI 的
``TestClient`` 直接打回调路由，orchestrator 用假对象替换，所以既能
覆盖路由挂载/验签，也能断言一条消息确实走到了 agent 回复路径。

加密相关除了自往返，还带一个**独立构造的已知答案向量**（用
cryptography 手工拼出企业微信的明文结构再加密），证明我们的
接收/发送两边和官方文档描述的字节布局一致，而不是只和自己自洽。
"""
from __future__ import annotations

import asyncio
import base64
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.deps import get_orchestrator
from api.routes import wecom as wecom_routes
from kairos import settings_store as _settings_store
from kairos.wecom import (
    DEFAULT_FORWARD_TOPICS,
    WeComBindingStore,
    WeComBot,
    WeComConfig,
    WeComEventForwarder,
    WeComSignatureError,
    compute_signature,
    decrypt_message,
    encrypt_message,
    parse_command,
    verify_signature,
)

# --------------------------------------------------------------------- 常量

# 43 字符的 EncodingAESKey（解码后 32 字节）。
AES_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY"
CORP_ID = "ww-test-corp"
TOKEN = "test-token-123"

# 独立构造的已知答案：明文 random(16)=bytes(range(16)) + len +
# b"KAIROS-KAT-OK" + b"ww-test-corp"，PKCS7(32) 后用上面的 key 做
# AES-256-CBC 加密。
KAT_CIPHERTEXT = (
    "oChxUuEMxdC3iAHYNh2h5fcxAiaSQP4Ro3VW5qKAU7B+t3rSpN00VlBxr5ighfLSSG1"
    "KQq2h86pwZKubc2LwKg=="
)


def _cfg(**over) -> Dict[str, Any]:
    base = dict(corp_id=CORP_ID, corp_secret="corp-secret",
                agent_id="1000002", token=TOKEN,
                encoding_aes_key=AES_KEY, enabled=True)
    base.update(over)
    return base


def _bot(**over) -> WeComBot:
    return WeComBot(WeComConfig(**_cfg(**over)))


# --------------------------------------------------------------------- fakes

class _FakeCoder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat(self, text: str, **kwargs: Any) -> str:
        self.calls.append(text)
        return f"reply: {text}"


class _FakeProject:
    def __init__(self, pid: str, name: str) -> None:
        self.id = pid
        self.name = name
        self.coder = _FakeCoder()


class _FakeOrchestrator:
    """最小 orchestrator：够路由创建/查询项目即可。"""

    def __init__(self) -> None:
        self.projects: Dict[str, _FakeProject] = {}
        self.created: list[str] = []

    def create_project(self, name: str, description: str = "",
                       work_dir: str = "") -> _FakeProject:
        pid = f"proj{len(self.projects) + 1}"
        project = _FakeProject(pid, name)
        self.projects[pid] = project
        self.created.append(name)
        return project

    def get_project(self, pid: str) -> Any:
        return self.projects.get(pid)

    def list_projects(self) -> list:
        return list(self.projects.values())


class _FakeResp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self) -> None:
        self.get_calls = 0
        self.post_calls = 0

    async def get(self, url: str, params: Any = None) -> _FakeResp:
        self.get_calls += 1
        return _FakeResp({"errcode": 0, "errmsg": "ok",
                          "access_token": "TOKEN-123", "expires_in": 7200})

    async def post(self, url: str, params: Any = None,
                   json: Any = None) -> _FakeResp:
        self.post_calls += 1
        return _FakeResp({"errcode": 0, "errmsg": "ok"})


class _FakeCtx:
    def __init__(self, client: _FakeHttpClient) -> None:
        self._client = client

    async def __aenter__(self) -> _FakeHttpClient:
        return self._client

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeHttpxModule:
    """替掉 WeComBot 内部 lazy import 的 httpx。"""

    def __init__(self, client: _FakeHttpClient) -> None:
        self._client = client

    def AsyncClient(self, **kwargs: Any) -> _FakeCtx:  # noqa: N802
        return _FakeCtx(self._client)


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    # 把设置存储指向 tmp，绝不碰仓库的 data/ 目录。
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    _settings_store.reset_store()

    store = WeComBindingStore(tmp_path / "wecom.db")
    asyncio.run(store.init())
    orch = _FakeOrchestrator()
    bot = _bot()

    wecom_routes.reset_dependencies()
    wecom_routes.set_dependencies(bot=bot, bindings=store)
    app.dependency_overrides[get_orchestrator] = lambda: orch
    client = TestClient(app)
    try:
        yield client, bot, store, orch
    finally:
        app.dependency_overrides.clear()
        wecom_routes.reset_dependencies()
        _settings_store.reset_store()


def _make_inbound(from_user: str, text: str, ts: str = "1700000000",
                  nonce: str = "nonce-1", msg_type: str = "text"):
    inner = (
        "<xml>"
        f"<ToUserName><![CDATA[{CORP_ID}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        "<CreateTime>1700000000</CreateTime>"
        f"<MsgType><![CDATA[{msg_type}]]></MsgType>"
        f"<Content><![CDATA[{text}]]></Content>"
        "<AgentID><![CDATA[1000002]]></AgentID>"
        "</xml>"
    )
    encrypt = encrypt_message(AES_KEY, inner, CORP_ID)
    signature = compute_signature(TOKEN, ts, nonce, encrypt)
    body = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>"
    params = {"msg_signature": signature, "timestamp": ts, "nonce": nonce}
    return body, params


def _reply_content(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    encrypt = root.find("Encrypt").text
    msg, _ = decrypt_message(AES_KEY, encrypt)
    inner = ET.fromstring(msg.decode("utf-8"))
    node = inner.find("Content")
    return node.text if node is not None else ""


# ------------------------------------------------------------------- 加解密

def test_aes_round_trip():
    ct = encrypt_message(AES_KEY, "你好，Kairos", CORP_ID)
    msg, receiveid = decrypt_message(AES_KEY, ct)
    assert msg.decode("utf-8") == "你好，Kairos"
    assert receiveid.decode("utf-8") == CORP_ID


def test_decrypt_matches_known_answer_vector():
    """独立构造的向量，证明字节布局符合企业微信文档。"""
    msg, receiveid = decrypt_message(AES_KEY, KAT_CIPHERTEXT)
    assert msg.decode("utf-8") == "KAIROS-KAT-OK"
    assert receiveid.decode("utf-8") == "ww-test-corp"


def test_encrypt_layout_is_independently_decryptable():
    """用 cryptography 手工拆一遍，确认 random+len+msg+receiveid 布局。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    ct = encrypt_message(AES_KEY, "hi", CORP_ID)
    key = base64.b64decode(AES_KEY + "=")
    dec = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    raw = dec.update(base64.b64decode(ct)) + dec.finalize()
    raw = raw[:-raw[-1]]  # 去 PKCS7
    msg_len = struct.unpack(">I", raw[16:20])[0]
    assert raw[20:20 + msg_len].decode("utf-8") == "hi"
    assert raw[20 + msg_len:].decode("utf-8") == CORP_ID


def test_bad_encoding_aes_key_is_rejected():
    from kairos.wecom import WeComCryptoError, _aes_key
    with pytest.raises(WeComCryptoError):
        _aes_key("too-short")


# --------------------------------------------------------------------- 签名

def test_signature_round_trip_and_reject():
    sig = compute_signature(TOKEN, "1700000000", "n1", "ENC")
    assert verify_signature(TOKEN, sig, "1700000000", "n1", "ENC") is True
    assert verify_signature(TOKEN, "deadbeef", "1700000000", "n1", "ENC") is False
    # token 未配置 → 失败关闭
    assert verify_signature("", sig, "1700000000", "n1", "ENC") is False


# ------------------------------------------------------------------ 命令解析

def test_parse_command_plain_text_becomes_chat():
    assert parse_command("你好 agent") == ("chat", ["你好 agent"])


def test_parse_command_slash():
    assert parse_command("/use proj1") == ("use", ["proj1"])
    assert parse_command("/help") == ("help", [])


# --------------------------------------------------------------- 主动发送/缓存

async def test_access_token_is_cached_not_refetched():
    fake = _FakeHttpClient()
    bot = _bot()
    bot._httpx = _FakeHttpxModule(fake)

    first = await bot.get_token()
    second = await bot.get_token()
    assert first == second == "TOKEN-123"
    assert fake.get_calls == 1

    # 发送两次也不会重新取 token。
    await bot.send_text("user-1", "hi")
    await bot.send_text("user-1", "again")
    assert fake.get_calls == 1
    assert fake.post_calls == 2


async def test_send_text_skips_when_disabled():
    bot = _bot(enabled=False)
    out = await bot.send_text("user-1", "hi")
    assert out["ok"] is False
    assert out["skipped"] == "wecom not configured"


async def test_send_text_requires_a_recipient():
    bot = _bot()
    bot._httpx = _FakeHttpxModule(_FakeHttpClient())
    assert (await bot.send_text("", "hi"))["skipped"] == "no touser"


# --------------------------------------------------------------- URL 验证

def test_bot_verify_url_returns_plaintext():
    bot = _bot()
    echostr = encrypt_message(AES_KEY, "echo-me", CORP_ID)
    sig = compute_signature(TOKEN, "170", "n1", echostr)
    assert bot.verify_url(sig, "170", "n1", echostr) == "echo-me"


def test_bot_verify_url_rejects_bad_signature():
    bot = _bot()
    echostr = encrypt_message(AES_KEY, "echo-me", CORP_ID)
    with pytest.raises(WeComSignatureError):
        bot.verify_url("wrong", "170", "n1", echostr)


async def test_http_verify_endpoint_returns_plain_echostr(env):
    client, *_ = env
    echostr = encrypt_message(AES_KEY, "hello-from-wecom", CORP_ID)
    sig = compute_signature(TOKEN, "1700000000", "nonce-9", echostr)
    r = client.get("/api/wecom/verify", params={
        "msg_signature": sig, "timestamp": "1700000000",
        "nonce": "nonce-9", "echostr": echostr})
    assert r.status_code == 200
    assert r.text == "hello-from-wecom"
    assert r.headers["content-type"].startswith("text/plain")


async def test_http_verify_endpoint_rejects_bad_signature(env):
    client, *_ = env
    echostr = encrypt_message(AES_KEY, "hello-from-wecom", CORP_ID)
    r = client.get("/api/wecom/verify", params={
        "msg_signature": "bad", "timestamp": "1700000000",
        "nonce": "nonce-9", "echostr": echostr})
    assert r.status_code == 401


# ------------------------------------------------------------------ 收消息

async def test_webhook_routes_text_message_to_agent_and_encrypts_reply(env):
    client, _bot_obj, store, orch = env
    body, params = _make_inbound("user-1", "hello world")
    r = client.post("/api/wecom/webhook", params=params, content=body,
                    headers={"Content-Type": "application/xml"})
    assert r.status_code == 200, r.text
    # 回复是被加密的企业微信 XML，解出来应带上 agent 的答复。
    assert _reply_content(r.text) == "reply: hello world"
    # 走到了 agent 回复路径：为这个成员建了项目并落了绑定。
    assert len(orch.projects) == 1
    bound = await store.lookup("user-1")
    assert bound in orch.projects
    assert orch.projects[bound].coder.calls == ["hello world"]


async def test_webhook_reuses_the_same_workspace_on_second_message(env):
    client, _bot_obj, store, orch = env
    b1, p1 = _make_inbound("user-1", "one")
    b2, p2 = _make_inbound("user-1", "two")
    client.post("/api/wecom/webhook", params=p1, content=b1)
    client.post("/api/wecom/webhook", params=p2, content=b2)
    assert len(orch.projects) == 1
    assert orch.projects["proj1"].coder.calls == ["one", "two"]


async def test_webhook_rejects_bad_signature(env):
    client, *_ = env
    body, params = _make_inbound("user-1", "hi")
    params["msg_signature"] = "tampered"
    r = client.post("/api/wecom/webhook", params=params, content=body)
    assert r.status_code == 401


async def test_webhook_ignores_non_text_and_empty_body(env):
    client, *_ = env
    # 非文本消息 → 空正文静默确认。
    body, params = _make_inbound("user-1", "img", msg_type="image")
    r = client.post("/api/wecom/webhook", params=params, content=body)
    assert r.status_code == 200
    assert r.text == ""
    # 没有 Encrypt 字段的 body → 也是空确认。
    r2 = client.post("/api/wecom/webhook", params=params,
                     content="<xml></xml>")
    assert r2.status_code == 200
    assert r2.text == ""


async def test_webhook_help_command(env):
    client, *_ = env
    body, params = _make_inbound("user-1", "/help")
    r = client.post("/api/wecom/webhook", params=params, content=body)
    assert r.status_code == 200
    assert "/status" in _reply_content(r.text)


async def test_webhook_unconfigured_returns_503(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
    _settings_store.reset_store()
    wecom_routes.reset_dependencies()  # 无注入 → 从（空）设置构造
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    client = TestClient(app)
    try:
        r = client.post("/api/wecom/webhook", content="<xml></xml>")
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()
        wecom_routes.reset_dependencies()
        _settings_store.reset_store()


# ------------------------------------------------------------------ 配置

async def test_config_put_then_get_never_leaks_secrets(env):
    client, *_ = env
    r = client.put("/api/wecom/config", json={
        "corp_id": "ww-new", "corp_secret": "SUPER-SECRET",
        "agent_id": "1000002", "token": "TOKEN-VALUE",
        "encoding_aes_key": AES_KEY, "enabled": True})
    assert r.status_code == 200
    assert "SUPER-SECRET" not in r.text
    assert "TOKEN-VALUE" not in r.text
    assert AES_KEY not in r.text

    g = client.get("/api/wecom/config")
    assert g.status_code == 200
    assert "SUPER-SECRET" not in g.text
    assert "TOKEN-VALUE" not in g.text
    assert AES_KEY not in g.text
    body = g.json()
    assert body["corp_id"] == "ww-new"
    assert body["enabled"] is True
    assert body["configured"] is True
    assert body["has_corp_secret"] is True
    assert body["has_token"] is True

    # 真的写进了设置（tmp 下的 settings.json），且可读回。
    assert _settings_store.get_store().get().wecom.corp_secret == "SUPER-SECRET"
    assert _settings_store.get_store().get().wecom.enabled is True


async def test_config_put_blank_secret_keeps_existing(env):
    client, *_ = env
    client.put("/api/wecom/config", json={
        "corp_id": "ww-new", "corp_secret": "KEEP-ME", "token": "KEEP-TOK",
        "encoding_aes_key": AES_KEY, "enabled": True})
    # 回显脱敏后再次提交（secret/token 留空）不能把密钥清空。
    client.put("/api/wecom/config", json={
        "corp_id": "ww-new", "enabled": True})
    w = _settings_store.get_store().get().wecom
    assert w.corp_secret == "KEEP-ME"
    assert w.token == "KEEP-TOK"
    assert w.encoding_aes_key == AES_KEY


# ------------------------------------------------------------------ 绑定/挂载

async def test_bindings_listed_and_removable(env):
    client, _bot_obj, _store, _orch = env
    body, params = _make_inbound("user-1", "hi")
    client.post("/api/wecom/webhook", params=params, content=body)
    listed = client.get("/api/wecom/bindings").json()["bindings"]
    assert len(listed) == 1
    assert listed[0]["chat_id"] == "user-1"
    assert client.delete("/api/wecom/bindings/user-1").status_code == 200
    assert client.get("/api/wecom/bindings").json()["bindings"] == []


def test_router_is_mounted():
    paths = set(app.openapi()["paths"])
    for expected in ("/api/wecom/config", "/api/wecom/verify",
                     "/api/wecom/webhook", "/api/wecom/test",
                     "/api/wecom/bindings", "/api/wecom/bindings/{chat_id}"):
        assert expected in paths, expected


# ------------------------------------------------------------------ 转发器

def test_default_forward_topics():
    assert {"loop_done", "user_input", "error"}.issubset(
        DEFAULT_FORWARD_TOPICS)


async def test_forwarder_formats_and_filters():
    bot = _bot()
    bot.send_text = _CountingSend()
    fwd = WeComEventForwarder(bot=bot, topics={"loop_done"},
                              to_user="user-1")
    assert "Title" in fwd._format(
        {"subject": "Title", "detail": "Body"}, "loop_done")

    class _Bus:
        def __init__(self, items):
            self._items = items

        async def subscribe(self):
            for item in self._items:
                yield item

    fwd.attach(_Bus([
        {"type": "loop_done", "subject": "done", "detail": "ok"},
        {"type": "plan_ready", "subject": "ignored", "detail": ""},
    ]))
    await fwd.start()
    await asyncio.sleep(0.05)
    await fwd.stop()
    assert bot.send_text.calls == [("user-1", "[Kairos] loop_done: done\nok")]


class _CountingSend:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def __call__(self, userid: str, text: str):
        self.calls.append((userid, text))
        return {"ok": True}


# ------------------------------------------------------------------ 统一分发

async def test_im_platform_dispatch_prefers_wecom_self_app(monkeypatch):
    """有 corp_id/agent_id 时，wecom 分发走自建应用而不是群机器人 webhook。"""
    from kairos import im_platforms

    calls: list[tuple] = []

    async def fake_send_text(self, userid: str, text: str):
        calls.append((userid, text))
        return {"ok": True, "via": "self-app"}

    monkeypatch.setattr("kairos.wecom.WeComBot.send_text", fake_send_text)
    cfg = im_platforms.IMConfig(wecom_corp_id="ww", wecom_corp_secret="s",
                                wecom_agent_id="1000002")
    out = await im_platforms.send_to_platform("wecom", cfg, "hi",
                                              chat_id="user-1")
    assert out["via"] == "self-app"
    assert calls == [("user-1", "hi")]
