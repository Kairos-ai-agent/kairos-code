"""微信官方 ClawBot / iLink 通道测试（全部离线）。

起一个本地 ``http.server`` 假扮 iLink 网关的 7 个端点，用真实的
``httpx`` 打它，所以测的是**真实的请求头 / 请求体 / 超时 / 错误码映射**，
而不是和自己自洽的桩。不连腾讯、不需要真实账号、不完成真实扫码登录。

覆盖：取码 → 轮询到成功 → 存 token；多账号隔离（各自游标 / 客户端 /
绑定不串）；收到消息 → 走 agent → 回复被发回（含 context_token 回带）；
游标回带；错误码映射；**token 绝不进任何 GET 响应 / 日志**。
"""
from __future__ import annotations

import asyncio
import base64
import http.server
import json
import logging
import threading
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.routes import weixin as weixin_routes
from kairos.weixin_ilink import (
    ILINK_CLIENT_VERSION,
    ILinkApiError,
    ILinkAuthError,
    ILinkClient,
    WeixinAccountStore,
    WeixinChannel,
    WeixinLoginSession,
    build_text_message,
    extract_text,
    is_user_message,
    parse_weixin_api_json,
)

SECRET_TOKEN = "BOT-SECRET-XYZ-123"


# ---------------------------------------------------------------------------
# 假 iLink 网关
# ---------------------------------------------------------------------------

class MockILink:
    """可脚本化的假网关。线程里跑 ``http.server``，用锁保护共享状态。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.qrcode = "QR-TEST-1"
        # 扫码状态脚本：每次 poll 弹一个；弹完用它自己的默认值。
        self.status_script: deque = deque()
        self.default_status = ("wait", {})
        # getupdates 脚本：每次弹一个响应；弹完用 self.default_updates。
        self.updates_script: deque = deque()
        self.default_updates: Dict[str, Any] = {"ret": 0, "msgs": []}
        self.force_send_error: Optional[Dict[str, Any]] = None
        # getupdates 的人为延迟（秒），用于测客户端长轮询超时。
        self.update_delay = 0.0

        self.requests: List[Dict[str, Any]] = []     # 每个请求的 method/path/headers/body
        self.send_bodies: List[Dict[str, Any]] = []  # sendmessage 的 msg
        self.received_cursors: List[str] = []        # getupdates 收到的游标
        self.notify: List[str] = []                  # notifystart/stop

        self._server: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: Optional[int] = None

    # -- 生命周期 -------------------------------------------------------

    def start(self) -> None:
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self._make_handler())
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- 脚本辅助 -------------------------------------------------------

    def script_status(self, *pairs: tuple) -> None:
        for p in pairs:
            self.status_script.append(p)

    def script_updates(self, *responses: Dict[str, Any]) -> None:
        for r in responses:
            self.updates_script.append(r)

    def last_send(self) -> Dict[str, Any]:
        with self.lock:
            return self.send_bodies[-1] if self.send_bodies else {}

    # -- 请求分发 -------------------------------------------------------

    def _make_handler(self):
        mock = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # 静音
                pass

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n > 0 else b""

            def _json(self, obj: Any, status: int = 200) -> None:
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                mock.handle(self, "GET", b"")

            def do_POST(self) -> None:  # noqa: N802
                mock.handle(self, "POST", self._body())

        return _Handler

    def handle(self, handler: http.server.BaseHTTPRequestHandler,
               method: str, raw_body: bytes) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        body: Dict[str, Any] = {}
        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except ValueError:
                body = {}
        headers = {k.lower(): v for k, v in handler.headers.items()}

        with self.lock:
            self.requests.append({"method": method, "path": path,
                                  "query": query, "headers": headers,
                                  "body": body})

        if path == "/ilink/bot/get_bot_qrcode":
            handler._json({"qrcode": self.qrcode,
                           "qrcode_img_content":
                               f"https://liteapp.weixin.qq.com/q/test?"
                               f"qrcode={self.qrcode}&bot_type=3",
                           "ret": 0})
        elif path == "/ilink/bot/get_qrcode_status":
            with self.lock:
                status, extra = (self.status_script.popleft()
                                 if self.status_script else self.default_status)
            handler._json({"ret": 0, "status": status, **extra})
        elif path == "/ilink/bot/getupdates":
            if self.update_delay:
                import time as _time
                _time.sleep(self.update_delay)
            buf = str(body.get("get_updates_buf") or "")
            with self.lock:
                self.received_cursors.append(buf)
                if self.updates_script:
                    resp = dict(self.updates_script.popleft())
                else:
                    resp = dict(self.default_updates)
            resp.setdefault("get_updates_buf", buf)
            handler._json(resp)
        elif path == "/ilink/bot/sendmessage":
            with self.lock:
                if self.force_send_error is not None:
                    handler._json(self.force_send_error)
                    return
                self.send_bodies.append(body.get("msg") or {})
            handler._json({"ret": 0, "errmsg": "ok", "message_id": "srv-999"})
        elif path == "/ilink/bot/getconfig":
            handler._json({"ret": 0, "typing_ticket": "TT-1"})
        elif path == "/ilink/bot/sendtyping":
            handler._json({"ret": 0})
        elif path == "/ilink/bot/msg/notifystart":
            with self.lock:
                self.notify.append("start")
            handler._json({"ret": 0})
        elif path == "/ilink/bot/msg/notifystop":
            with self.lock:
                self.notify.append("stop")
            handler._json({"ret": 0})
        else:
            handler._json({"ret": -1, "errmsg": "unknown endpoint"}, status=404)


@pytest.fixture
def mock_ilink():
    m = MockILink()
    m.start()
    try:
        yield m
    finally:
        m.stop()


def _client(mock: MockILink, token: Optional[str] = None) -> ILinkClient:
    return ILinkClient(base_url=mock.base_url, token=token)


async def _new_store(tmp_path: Path, name: str = "weixin.db") -> WeixinAccountStore:
    store = WeixinAccountStore(tmp_path / name)
    await store.init()
    return store


def _user_message(text: str, *, from_user: str = "user-1",
                  context_token: Optional[str] = None) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "message_type": 1,
        "from_user_id": from_user,
        "to_user_id": "bot",
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }
    if context_token:
        msg["context_token"] = context_token
    return msg


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------

def test_parse_preserves_large_uint64_id_as_string():
    raw = ('{"ret":0,"msgs":[{"message_id":18446744073709551615,'
           '"msg_id":12345,"from_user_id":"u"}]}')
    data = parse_weixin_api_json(raw)
    assert data["msgs"][0]["message_id"] == "18446744073709551615"
    assert data["msgs"][0]["msg_id"] == "12345"
    assert data["msgs"][0]["from_user_id"] == "u"


def test_parse_does_not_rewrite_id_lookalike_inside_strings():
    raw = '{"text":"message_id: 12345678901234567890"}'
    assert parse_weixin_api_json(raw)["text"] == \
        "message_id: 12345678901234567890"


def test_message_helpers():
    assert extract_text(_user_message("hi")) == "hi"
    assert extract_text({"item_list": [{"type": 3,
                                        "voice_item": {"text": "语音转写"}}]}) == "语音转写"
    assert extract_text({"item_list": [{"type": 2}]}) == "[图片]"
    assert is_user_message(_user_message("hi")) is True
    assert is_user_message({"message_type": 2, "from_user_id": "bot",
                            "item_list": [{"type": 1,
                                           "text_item": {"text": "x"}}]}) is False
    assert is_user_message({"message_type": 1, "from_user_id": "",
                            "item_list": [{"type": 1,
                                           "text_item": {"text": "x"}}]}) is False


def test_build_text_message_shape():
    msg = build_text_message("user-1", "hello", context_token="CTX")
    assert msg["message_type"] == 2 and msg["message_state"] == 2
    assert msg["to_user_id"] == "user-1" and msg["from_user_id"] == ""
    assert msg["item_list"][0]["text_item"]["text"] == "hello"
    assert msg["context_token"] == "CTX"
    assert msg["client_id"].startswith("kairos-weixin-")


# ---------------------------------------------------------------------------
# 请求头
# ---------------------------------------------------------------------------

async def test_client_sends_required_headers_and_bearer(mock_ilink):
    client = _client(mock_ilink, token="TOK-123")
    await client.get_updates("")
    h = mock_ilink.requests[-1]["headers"]
    assert h["ilink-app-id"] == "bot"
    assert h["ilink-app-clientversion"] == str(ILINK_CLIENT_VERSION)
    assert h["authorizationtype"] == "ilink_bot_token"
    assert h["authorization"] == "Bearer TOK-123"
    assert h["content-type"] == "application/json"
    # X-WECHAT-UIN 是 base64(十进制随机 uint32)
    assert base64.b64decode(h["x-wechat-uin"]).decode().isdigit()


async def test_login_endpoints_do_not_require_a_token(mock_ilink):
    client = _client(mock_ilink, token=None)
    qr = await client.get_bot_qrcode()
    assert qr["qrcode"] == "QR-TEST-1"
    assert "qrcode" in qr["qrcode_img_content"]
    h = mock_ilink.requests[-1]["headers"]
    assert "authorization" not in h  # 登录前不带 Bearer
    await client.get_qrcode_status(qr["qrcode"])
    assert "authorization" not in mock_ilink.requests[-1]["headers"]


# ---------------------------------------------------------------------------
# 登录流程
# ---------------------------------------------------------------------------

async def test_qrcode_flow_confirms_returns_token_and_hides_it(
        mock_ilink, tmp_path, caplog):
    mock_ilink.script_status(
        ("wait", {}),
        ("scaned", {}),
        ("confirmed", {"bot_token": SECRET_TOKEN, "ilink_bot_id": "acct-1",
                       "ilink_user_id": "user-1", "baseurl": mock_ilink.base_url}),
    )
    # 只捕获本模块的日志，确认 token 不会写进日志。
    records: List[str] = []
    handler = logging.Handler()
    handler.emit = lambda rec: records.append(rec.getMessage())  # type: ignore[assignment]
    mod_logger = logging.getLogger("kairos.weixin_ilink")
    mod_logger.addHandler(handler)
    mod_logger.setLevel(logging.DEBUG)

    try:
        client = _client(mock_ilink, token=None)
        session = WeixinLoginSession(client)
        state = await session.start()
        assert state["qrcode"] == "QR-TEST-1"
        assert "token" not in json.dumps(state)
        assert session.token is None

        seen = []
        while not session.connected and session.status != "error":
            seen.append(await session.poll())
        assert "confirmed" in seen
        assert session.token == SECRET_TOKEN
        assert session.account_id == "acct-1"
        assert session.user_id == "user-1"

        # public_state / 日志里都没有 token
        assert "token" not in session.public_state()
        assert SECRET_TOKEN not in json.dumps(session.public_state())
        assert all(SECRET_TOKEN not in m for m in records)

        # 落库：token 只出现在单独的 get_credentials()
        store = await _new_store(tmp_path)
        await store.upsert_account(session.account_id, token=session.token,
                                   user_id=session.user_id,
                                   base_url=session.base_url_resolved,
                                   status="online")
        creds = await store.get_credentials("acct-1")
        assert creds["token"] == SECRET_TOKEN
        listed = await store.list_accounts()
        assert SECRET_TOKEN not in json.dumps([a.to_dict() for a in listed])
    finally:
        mod_logger.removeHandler(handler)


async def test_expired_qrcode_is_refreshed(mock_ilink):
    mock_ilink.script_status(("expired", {}), ("wait", {}))
    client = _client(mock_ilink, token=None)
    session = WeixinLoginSession(client)
    await session.start()
    first_qr = session.qrcode
    await session.poll()   # expired → 自动刷新
    assert session.qrcode is not None
    # 刷新后仍在轮询（不是直接失败）
    assert session.status in ("wait", "expired")
    assert first_qr == "QR-TEST-1"


async def test_scaned_but_redirect_switches_host(mock_ilink):
    mock_ilink.script_status(("scaned_but_redirect",
                              {"redirect_host": "ilinkai-2.example.com"}),
                             ("wait", {}))
    client = _client(mock_ilink, token=None)
    session = WeixinLoginSession(client)
    await session.start()
    await session.poll()
    assert session.client.base_url == "https://ilinkai-2.example.com/"


# ---------------------------------------------------------------------------
# 游标 / 错误码
# ---------------------------------------------------------------------------

async def test_get_updates_cursor_round_trip(mock_ilink):
    mock_ilink.script_updates({"ret": 0, "get_updates_buf": "CUR-Z", "msgs": []})
    client = _client(mock_ilink, token="T")
    r1 = await client.get_updates("")
    assert r1["get_updates_buf"] == "CUR-Z"
    r2 = await client.get_updates(r1["get_updates_buf"])
    assert mock_ilink.received_cursors[-1] == "CUR-Z"
    # 脚本弹空后：默认响应把收到的游标原样回带
    assert r2["get_updates_buf"] == "CUR-Z"


async def test_errcode_minus_14_maps_to_auth_error(mock_ilink):
    mock_ilink.script_updates({"errcode": -14, "errmsg": "session timeout"})
    client = _client(mock_ilink, token="T")
    with pytest.raises(ILinkAuthError):
        await client.get_updates("")


async def test_sendmessage_nonzero_ret_maps_to_api_error(mock_ilink):
    mock_ilink.force_send_error = {"ret": 5, "errmsg": "boom"}
    client = _client(mock_ilink, token="T")
    with pytest.raises(ILinkApiError):
        await client.send_message(build_text_message("u", "hi"))


async def test_getupdates_client_timeout_returns_empty(mock_ilink):
    """长轮询客户端超时应是正常控制流（空响应），不是异常。"""
    mock_ilink.update_delay = 0.4
    client = ILinkClient(base_url=mock_ilink.base_url, token="T",
                         long_poll_timeout=0.05)
    resp = await client.get_updates("KEEP-ME")
    assert resp["ret"] == 0 and resp["msgs"] == []
    assert resp["get_updates_buf"] == "KEEP-ME"


# ---------------------------------------------------------------------------
# 多账号隔离
# ---------------------------------------------------------------------------

async def test_two_accounts_have_separate_clients_cursors_bindings(
        mock_ilink, tmp_path):
    store = await _new_store(tmp_path)
    await store.upsert_account("acct-A", token="TOK-A", status="online")
    await store.upsert_account("acct-B", token="TOK-B", status="online")
    await store.save_cursor("acct-A", "CURSOR-A")
    await store.save_cursor("acct-B", "CURSOR-B")
    assert await store.load_cursor("acct-A") == "CURSOR-A"
    assert await store.load_cursor("acct-B") == "CURSOR-B"

    channel = WeixinChannel(
        store,
        client_factory=lambda token, base_url: ILinkClient(
            base_url=mock_ilink.base_url, token=token))
    ca = await channel.client_for("acct-A")
    cb = await channel.client_for("acct-B")
    assert ca is not cb
    assert ca._token == "TOK-A" and cb._token == "TOK-B"

    # 同一个聊天对象 "user-1" 在两个账号下各自绑定独立项目
    await store.bind("acct-A", "user-1", "proj-A")
    await store.bind("acct-B", "user-1", "proj-B")
    assert await store.lookup("acct-A", "user-1") == "proj-A"
    assert await store.lookup("acct-B", "user-1") == "proj-B"

    # 两个账号各自用自己的 token 发消息
    await channel.send_text("acct-A", "user-1", "from A")
    await channel.send_text("acct-B", "user-1", "from B")
    sends = [(r["headers"].get("authorization"),
              r["body"].get("msg", {}).get("item_list", [{}])[0]
              .get("text_item", {}).get("text"))
             for r in mock_ilink.requests
             if r["path"] == "/ilink/bot/sendmessage"]
    assert ("Bearer TOK-A", "from A") in sends
    assert ("Bearer TOK-B", "from B") in sends


# ---------------------------------------------------------------------------
# 收消息 → agent → 发回
# ---------------------------------------------------------------------------

async def test_inbound_message_reaches_agent_and_reply_is_sent(
        mock_ilink, tmp_path):
    store = await _new_store(tmp_path)
    await store.upsert_account("acct-A", token="TOK-A", status="online")
    calls: List[tuple] = []

    async def dispatch(account_id: str, chat_id: str, text: str) -> str:
        calls.append((account_id, chat_id, text))
        return f"reply: {text}"

    channel = WeixinChannel(
        store, dispatch=dispatch,
        client_factory=lambda token, base_url: ILinkClient(
            base_url=mock_ilink.base_url, token=token))
    client = await channel.client_for("acct-A")

    reply = await channel.handle_message(
        "acct-A", client,
        _user_message("hello", context_token="CTX-9"))

    assert reply == "reply: hello"
    assert calls == [("acct-A", "user-1", "hello")]
    sent = mock_ilink.last_send()
    assert sent["to_user_id"] == "user-1"
    assert sent["item_list"][0]["text_item"]["text"] == "reply: hello"
    assert sent["context_token"] == "CTX-9"      # 原样回带
    assert sent["message_type"] == 2             # BOT
    # context_token 也落了库
    assert await store.get_context_token("acct-A", "user-1") == "CTX-9"


async def test_bot_and_empty_messages_are_ignored(mock_ilink, tmp_path):
    store = await _new_store(tmp_path)
    await store.upsert_account("acct-A", token="TOK-A")
    calls: List[tuple] = []

    async def dispatch(a, c, t):
        calls.append((a, c, t))
        return "x"

    channel = WeixinChannel(store, dispatch=dispatch,
                            client_factory=lambda token, base_url: ILinkClient(
                                base_url=mock_ilink.base_url, token=token))
    client = await channel.client_for("acct-A")
    assert await channel.handle_message(
        "acct-A", client, {"message_type": 2, "from_user_id": "bot"}) is None
    assert await channel.handle_message(
        "acct-A", client, {"message_type": 1, "from_user_id": "u",
                           "item_list": []}) is None
    assert calls == []
    assert mock_ilink.send_bodies == []


async def test_poll_loop_consumes_updates_uses_cursor_and_replies(
        mock_ilink, tmp_path):
    store = await _new_store(tmp_path)
    await store.upsert_account("acct-A", token="TOK-A", status="online")
    mock_ilink.script_updates({
        "ret": 0, "get_updates_buf": "CUR-1",
        "msgs": [_user_message("ping", context_token="CTX-1")],
    })

    async def dispatch(a, c, t):
        return f"pong:{t}"

    channel = WeixinChannel(
        store, dispatch=dispatch, poll_interval=0.05,
        client_factory=lambda token, base_url: ILinkClient(
            base_url=mock_ilink.base_url, token=token))

    await channel.start_account("acct-A")
    try:
        # 等第一条回复被发回，以及第二次 getupdates 把新游标带上来。
        for _ in range(300):
            if mock_ilink.send_bodies and "CUR-1" in mock_ilink.received_cursors:
                break
            await asyncio.sleep(0.02)
    finally:
        await channel.stop_account("acct-A")

    assert mock_ilink.send_bodies, "agent 回复没有被发回"
    assert mock_ilink.last_send()["item_list"][0]["text_item"]["text"] == "pong:ping"
    # 游标已落库，并回带给了下一次 getupdates
    assert await store.load_cursor("acct-A") == "CUR-1"
    assert "CUR-1" in mock_ilink.received_cursors
    # notifystart / notifystop 都发过
    assert "start" in mock_ilink.notify and "stop" in mock_ilink.notify


async def test_channel_marks_account_error_on_auth_failure(mock_ilink, tmp_path):
    store = await _new_store(tmp_path)
    await store.upsert_account("acct-A", token="DEAD", status="online")
    mock_ilink.script_updates({"errcode": -14, "errmsg": "session timeout"})
    channel = WeixinChannel(
        store, poll_interval=0.05,
        client_factory=lambda token, base_url: ILinkClient(
            base_url=mock_ilink.base_url, token=token))
    await channel.start_account("acct-A")
    for _ in range(100):
        acct = await store.get_account("acct-A")
        if acct.status == "error":
            break
        await asyncio.sleep(0.02)
    assert (await store.get_account("acct-A")).status == "error"
    await channel.stop_account("acct-A")


# ---------------------------------------------------------------------------
# REST 接口
# ---------------------------------------------------------------------------

class _FakeProject:
    def __init__(self, pid: str, name: str) -> None:
        self.id = pid
        self.name = name


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.projects: Dict[str, _FakeProject] = {}

    def create_project(self, name: str, description: str = "",
                       work_dir: str = "") -> _FakeProject:
        pid = f"proj{len(self.projects) + 1}"
        self.projects[pid] = _FakeProject(pid, name)
        return self.projects[pid]

    def get_project(self, pid: str) -> Any:
        return self.projects.get(pid)

    def list_projects(self) -> list:
        return list(self.projects.values())


class _RecordingChannel(WeixinChannel):
    """REST 测试用：记录启停但不真的起后台协程（避免跨事件循环的任务泄漏）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.started: List[str] = []

    async def start_account(self, account_id: str, *,
                            client: Optional[ILinkClient] = None) -> None:
        if account_id not in self.started:
            self.started.append(account_id)
        await self.store.set_status(account_id, "online")

    async def stop_account(self, account_id: str) -> None:
        if account_id in self.started:
            self.started.remove(account_id)
        await self.store.set_status(account_id, "offline")


@pytest.fixture
def rest_env(mock_ilink, tmp_path):
    store = WeixinAccountStore(tmp_path / "weixin.db")
    asyncio.run(store.init())
    orch = _FakeOrchestrator()

    async def dispatch(a, c, t):
        return f"reply: {t}"

    channel = _RecordingChannel(
        store, dispatch=dispatch,
        client_factory=lambda token, base_url: ILinkClient(
            base_url=mock_ilink.base_url, token=token))

    weixin_routes.reset_dependencies()
    weixin_routes.set_dependencies(
        channel=channel, store=store,
        login_client_factory=lambda: ILinkClient(base_url=mock_ilink.base_url,
                                                 token=None))
    client = TestClient(app)
    try:
        yield client, store, channel, orch, mock_ilink
    finally:
        weixin_routes.reset_dependencies()


def test_router_is_mounted():
    paths = set(app.openapi()["paths"])
    for expected in ("/api/weixin/login/start", "/api/weixin/login/status",
                     "/api/weixin/login/qr.png",
                     "/api/weixin/accounts",
                     "/api/weixin/accounts/{account_id}",
                     "/api/weixin/accounts/{account_id}/send",
                     "/api/weixin/bindings"):
        assert expected in paths, expected


def test_login_qr_png_returns_scannable_png_and_never_leaks_token(rest_env):
    """qr.png 出的是真 PNG（魔数对），且图里没有 token。"""
    client, store, channel, orch, mock = rest_env
    # 省略 qrcode：一次请求就现取一张码并出图。
    r = client.get("/api/weixin/login/qr.png")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    # 字节以 PNG 魔数开头。
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(r.content) > 100
    # token 绝不在图片字节里（这里是登录前，更不该有）。
    assert SECRET_TOKEN.encode() not in r.content
    # 图片回带了二维码 id，前端不用再多打一次 login/start。
    assert r.headers.get("x-weixin-qrcode") == "QR-TEST-1"

    # 用这个 id 再取一次：命中缓存，字节完全一致。
    r2 = client.get("/api/weixin/login/qr.png", params={"qrcode": "QR-TEST-1"})
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "image/png"
    assert r2.content == r.content


def test_login_qr_png_encodes_the_qrcode_url_not_a_token(rest_env):
    client, *_ = rest_env
    client.get("/api/weixin/login/qr.png")
    session = weixin_routes._sessions["QR-TEST-1"]
    # 会话里存的是 liteapp 链接；图片编码的就是它，且会话还没有 token。
    assert session.qrcode_url and "liteapp.weixin.qq.com" in session.qrcode_url
    assert session.token is None


def test_login_qr_png_unknown_qrcode_is_404(rest_env):
    client, *_ = rest_env
    r = client.get("/api/weixin/login/qr.png", params={"qrcode": "nope"})
    assert r.status_code == 404


def test_login_start_returns_qr_and_status_never_leaks_token(rest_env):
    client, store, channel, orch, mock = rest_env
    mock.script_status(
        ("wait", {}),
        ("scaned", {}),
        ("confirmed", {"bot_token": SECRET_TOKEN, "ilink_bot_id": "acct-9",
                       "ilink_user_id": "user-9", "baseurl": mock.base_url}),
    )
    r = client.post("/api/weixin/login/start", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qrcode"] == "QR-TEST-1"
    assert "qrcode" in body["qrcode_url"]
    assert SECRET_TOKEN not in r.text

    qid = body["qrcode"]
    # 三次轮询：wait → scaned → confirmed
    for _ in range(3):
        s = client.get("/api/weixin/login/status", params={"qrcode": qid})
        assert s.status_code == 200
        assert SECRET_TOKEN not in s.text     # 关键：任何一次都不含 token
    assert s.json()["status"] == "confirmed"
    assert s.json()["connected"] is True    # confirmed 后 connected 为真

    # token 落库了（单独读取）
    creds = asyncio.run(store.get_credentials("acct-9"))
    assert creds["token"] == SECRET_TOKEN
    # 账号被登记为在线，且要求启动了后台轮询
    assert asyncio.run(store.get_account("acct-9")).status == "online"
    assert "acct-9" in channel.started


def test_accounts_endpoint_is_desensitized(rest_env):
    client, store, *_ = rest_env
    asyncio.run(store.upsert_account("acct-1", token=SECRET_TOKEN,
                                     user_id="user-1", status="online"))
    r = client.get("/api/weixin/accounts")
    assert r.status_code == 200
    assert SECRET_TOKEN not in r.text
    body = r.json()
    assert body["accounts"][0]["id"] == "acct-1"
    assert body["accounts"][0]["user_id"] == "user-1"
    assert "token" not in body["accounts"][0]


def test_unknown_login_session_returns_404(rest_env):
    client, *_ = rest_env
    r = client.get("/api/weixin/login/status", params={"qrcode": "nope"})
    assert r.status_code == 404


def test_delete_account_removes_it(rest_env):
    client, store, *_ = rest_env
    asyncio.run(store.upsert_account("acct-1", token="T", status="online"))
    r = client.delete("/api/weixin/accounts/acct-1")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/weixin/accounts").json()["accounts"] == []


def test_send_endpoint_uses_account_and_never_leaks_token(rest_env):
    client, store, channel, orch, mock = rest_env
    asyncio.run(store.upsert_account("acct-1", token=SECRET_TOKEN,
                                     status="online"))
    r = client.post("/api/weixin/accounts/acct-1/send",
                    json={"to": "user-1", "text": "hi there"})
    assert r.status_code == 200, r.text
    assert SECRET_TOKEN not in r.text
    assert mock.last_send()["to_user_id"] == "user-1"
    assert mock.last_send()["item_list"][0]["text_item"]["text"] == "hi there"


def test_send_endpoint_requires_recipient(rest_env):
    client, store, *_ = rest_env
    asyncio.run(store.upsert_account("acct-1", token="T", status="online"))
    r = client.post("/api/weixin/accounts/acct-1/send", json={"text": "hi"})
    assert r.status_code == 400


def test_bindings_endpoint_lists_account_scoped_bindings(rest_env):
    client, store, *_ = rest_env
    asyncio.run(store.upsert_account("acct-1", token="T", status="online"))
    asyncio.run(store.bind("acct-1", "user-1", "proj-1"))
    r = client.get("/api/weixin/bindings")
    assert r.status_code == 200
    assert r.json()["bindings"][0]["project_id"] == "proj-1"


def test_accounts_endpoint_503_when_not_initialized(tmp_path, monkeypatch):
    weixin_routes.reset_dependencies()
    client = TestClient(app)
    try:
        r = client.get("/api/weixin/accounts")
        assert r.status_code == 503
    finally:
        weixin_routes.reset_dependencies()
