"""End-to-end test for the WeChatPadPro connector, driven by a stub gateway.

The real WeChatPadPro gateway needs an ADMIN_KEY and a scanned login before it
will talk to anything, and neither is available in this environment. So this
module proves the connector against a **stub gateway**: a local HTTP server
that implements the same endpoints the connector calls
(``/admin/GenAuthKey1``, ``/login/GetLoginQrCodeA16``, ``/webhook/Config``,
``/message/SendTextMessage`` …) and, crucially, **pushes webhook events to the
connector** the way the real gateway does.

The Kairos app is the **real app** -- launched the way
``scripts/smoke_binary.py`` launches it (isolated ``LOCALAPPDATA``,
``--port 9555``), with a fake OpenAI-compatible endpoint behind it -- so the
signing, the per-conversation project, the outbound queue and the ack are all
the real thing, not a mock.

What is proven here:

1. the connector mints a key from the stub's ``ADMIN_KEY`` (and models the
   real gateway's "JSON body required" quirk);
2. preflight refuses to start with no authorization code and no reachable
   gateway, before any pairing is claimed;
3. a pairing through the gateway creates a Kairos account, uploads the QR the
   gateway returned, and leaves the account secret only in ``--secret-out``;
4. a correct account HMAC is accepted and a wrong one is refused (401);
5. a signed webhook push becomes a signed inbound call, the agent replies,
   the connector sends it through the gateway and acks it;
6. an unsigned/forged webhook push is dropped and the connector stays up;
7. when the gateway answers a send with an error Code the connector does not
   crash, does not ack, and retries;
8. the ``stub`` backend still runs the whole protocol path with no gateway.

``connectors/`` is deliberately not in the public tree, so this module skips
with an explicit reason when the connector is not present.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONNECTOR = REPO / "connectors" / "wechat_padpro.py"
HOOK = REPO / "connectors" / "wechat_hook.py"
APP_PORT = 9555
FAKE_LLM_PREFIX = "FAKE-LLM-REPLY: "

#: 1x1 PNG，当桩网关的「登录二维码」用。
STUB_QR_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

pytestmark = pytest.mark.skipif(
    not CONNECTOR.is_file() or not HOOK.is_file(),
    reason=(f"{CONNECTOR} is absent. The WeChat connector is a local-only "
            "component (see the repository .gitignore and connectors/README.md); "
            "this suite can only run on a machine that has it."))


# --------------------------------------------------------------------- pieces

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until(predicate, timeout: float, what: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.3)
    raise AssertionError(f"timed out after {timeout}s waiting for: {what}")


class _FakeLLM:
    """A minimal OpenAI-compatible endpoint. ``/v1/chat/completions`` only."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.requests: list = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):  # keep pytest output clean
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    body = {}
                outer.requests.append(body)
                last = ""
                for message in body.get("messages") or []:
                    if message.get("role") == "user":
                        last = str(message.get("content") or "")
                payload = json.dumps({
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body.get("model") or "gpt-4o",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant",
                                    "content": FAKE_LLM_PREFIX + last},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 7,
                              "total_tokens": 14},
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class StubGateway:
    """A stand-in for WeChatPadPro.

    It answers the endpoints the connector calls, records what it was asked,
    and can push a webhook event to whatever callback URL the connector
    registered -- which is how the receive path is exercised without a real
    WeChat login.
    """

    ADMIN_KEY = "admin-stub-key"
    AUTH_KEY = "auth-stub-key"
    WXID = "wxpad-stub-user"
    NICK = "桩网关微信"

    def __init__(self) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.qr_requests = 0
        self.auth_key_calls = []          # {"key":..., "body_present":...}
        self.webhook_config: dict = {}
        self.sent_messages: list = []     # {"Wxid":..., "Content":...}
        self.fail_sends = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _json(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _not_found(self) -> None:
                body = b"404 page not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> tuple:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    return raw, json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return raw, {}

            def do_GET(self) -> None:
                path = urllib.parse.urlparse(self.path).path
                if path == "/webhook/List":
                    self._json(200, {"Code": 200, "Data": dict(outer.webhook_config),
                                     "Text": "获取Webhook配置列表成功"})
                else:
                    self._not_found()

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                query = urllib.parse.parse_qs(parsed.query)
                key = (query.get("key") or [""])[0]
                raw, body = self._body()

                if path == "/admin/GenAuthKey1":
                    # 真网关的两个坑：空 body 报 EOF；只给 query 报 400。
                    outer.auth_key_calls.append(
                        {"key": key, "body_present": bool(raw)})
                    if key != outer.ADMIN_KEY:
                        self._json(200, {"Code": 300, "Data": None,
                                         "Text": "未提供有效的认证参数"})
                    elif not raw:
                        self._json(200, {"Code": 300, "Data": None,
                                         "Text": "参数错误: EOF"})
                    else:
                        self._json(200, {"Code": 200,
                                         "Data": {"Key": outer.AUTH_KEY},
                                         "Text": "成功"})
                    return

                if path in ("/login/GetLoginQrCodeA16",
                            "/login/GetLoginQrCodeCar",
                            "/login/GetLoginQrCodeAndroidPad"):
                    if key != outer.AUTH_KEY:
                        self._json(200, {"Code": 300, "Data": None,
                                         "Text": "请提供有效的授权码"})
                        return
                    outer.qr_requests += 1
                    self._json(200, {"Code": 200, "Text": "成功", "Data": {
                        "QRCode": base64.b64encode(STUB_QR_PNG).decode(),
                        "Wxid": outer.WXID, "NickName": outer.NICK}})
                    return

                if path == "/webhook/Config":
                    outer.webhook_config = body if isinstance(body, dict) else {}
                    self._json(200, {"Code": 200, "Data": {},
                                     "Text": "配置成功"})
                    return

                if path == "/message/SendTextMessage":
                    if outer.fail_sends:
                        self._json(200, {"Code": 300, "Data": None,
                                         "Text": "模拟发送失败"})
                        return
                    outer.sent_messages.append({
                        "Wxid": body.get("Wxid"),
                        "Content": body.get("Content"),
                    })
                    self._json(200, {"Code": 200, "Data": {}, "Text": "成功"})
                    return

                if path == "/friend/GetContactList":
                    self._json(200, {"Code": 200, "Data": {}, "Text": "成功"})
                    return

                self._not_found()

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- 主动推送 --------------------------------------------------------

    def callback_url(self) -> str:
        return str(self.webhook_config.get("URL") or "")

    def push_message_event(self, chat_id: str, text: str,
                           message_id: str = "stub-msg-1",
                           signed: bool = True) -> int:
        """把一条「收到消息」事件 POST 给连接器，就像真网关那样。"""
        url = self.callback_url()
        assert url, "the connector never registered a callback URL"
        ts = int(time.time())
        event = {
            "event_type": "message",
            "timestamp": ts,
            "Wxid": chat_id,
            "MessageType": "1",
            "Timestamp": ts,
            "data": {
                "MsgType": 1,
                "FromUserName": {"string": chat_id},
                "ToUserName": {"string": self.WXID},
                "Content": {"string": text},
                "MsgId": message_id,
                "CreateTime": ts,
            },
        }
        secret = str(self.webhook_config.get("Secret") or "")
        if signed and secret:
            base = f"{chat_id}:1:{ts}"
            event["Signature"] = hmac.new(
                secret.encode("utf-8"), base.encode("utf-8"),
                hashlib.sha256).hexdigest()
        elif not signed:
            event["Signature"] = "0000deadbeef0000"
        req = urllib.request.Request(
            url, data=json.dumps(event).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code


def _terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def _get(url: str, timeout: float = 5.0) -> tuple:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - not up yet
        return 0, f"{type(exc).__name__}: {exc}"


def _json_get(base: str, path: str) -> dict:
    code, body = _get(base + path)
    assert code == 200, f"GET {path} -> {code} {body[:200]}"
    return json.loads(body)


def _post_json(base: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _log_lines(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line]


def _has_direction(path: Path, direction: str) -> bool:
    return any(line.get("direction") == direction for line in _log_lines(path))


# -------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def fake_llm() -> _FakeLLM:
    server = _FakeLLM()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def app_base(tmp_path_factory, fake_llm):
    """The real app, isolated, on --port 9555, with a fake model behind it."""
    local_appdata = tmp_path_factory.mktemp("localappdata-wxpadpro")
    data_dir = local_appdata / "kairos-code" / "data"
    data_dir.mkdir(parents=True)
    (local_appdata / "kairos-code" / "logs").mkdir(parents=True)
    (data_dir / "settings.json").write_text(json.dumps({
        "provider": {
            "active": "openai",
            "openai": {
                "apiKey": "test-key-not-a-real-one",
                "model": "gpt-4o",
                "endpointUrl": (f"http://127.0.0.1:{fake_llm.port}"
                                "/v1/chat/completions"),
            },
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_appdata)
    env["KAIROS_NO_BUNDLED_MCP"] = "1"
    env["KAIROS_NO_CHECKPOINTS"] = "1"
    env["KAIROS_INSIDE_TESTS"] = "1"
    env.pop("KAIROS_DATA_DIR", None)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        env.pop(key, None)

    log_path = local_appdata / "app.log"
    log = log_path.open("wb")
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "kairos_code_launcher.py"),
         "--no-browser", "--host", "127.0.0.1", "--port", str(APP_PORT)],
        cwd=str(REPO), env=env, stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL)

    def fail(reason: str):
        log.flush()
        text = log_path.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(text.splitlines()[-40:])
        return pytest.fail(f"{reason}\n--- app output (last 40 lines) ---\n{tail}")

    base = f"http://127.0.0.1:{APP_PORT}"
    try:
        deadline = time.time() + 150.0
        healthy = False
        while time.time() < deadline:
            if proc.poll() is not None:
                fail(f"the app exited early with code {proc.returncode}")
            if _get(f"{base}/api/health")[0] == 200:
                healthy = True
                break
            time.sleep(1.0)
        if not healthy:
            fail("the app never answered /api/health within 150s")
        log.flush()
        text = log_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"web:\s+(http://\S+)", text)
        if match and match.group(1) != base:
            fail(f"the app did not come up on --port {APP_PORT} but on "
                 f"{match.group(1)} — free that port and re-run")
        status = _json_get(base, "/api/im/status")
        assert status["initialized"], "the IM store is not wired in the app"
        yield base
    finally:
        _terminate_tree(proc)
        log.close()


@pytest.fixture
def stub_gateway() -> StubGateway:
    server = StubGateway()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def connector_module():
    """The connector as a module, for its own client/backend helpers."""
    spec = importlib.util.spec_from_file_location("wechat_padpro_connector",
                                                  CONNECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def secrets_dir(tmp_path) -> Path:
    return tmp_path / "secrets"


# ------------------------------------------------------------------- helpers

def _run_connector(base: str, *args: str, timeout: float = 180.0,
                   ) -> subprocess.CompletedProcess:
    """Create a Kairos pairing, then run the connector over it.

    The pairing is created first because that is the operator's order: click
    'connect' in the app, then start the connector (which claims it).
    """
    _post_json(base, "/api/im/pairings", {})
    cmd = [sys.executable, str(CONNECTOR), "--base-url", base, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=str(REPO), encoding="utf-8", errors="replace")


def _pair_real(base: str, secret_file: Path, account_id: str,
               gateway: StubGateway, display_name: str = "网关微信",
               ) -> subprocess.CompletedProcess:
    return _run_connector(
        base, "--backend", "real", "--gateway-url", gateway.url,
        "--auth-key", gateway.AUTH_KEY,
        "--account-id", account_id, "--display-name", display_name,
        "--secret-out", str(secret_file), "--pair-only")


def _pair_stub(base: str, secret_file: Path, account_id: str,
               ) -> subprocess.CompletedProcess:
    return _run_connector(
        base, "--backend", "stub", "--account-id", account_id,
        "--display-name", "桩后端", "--secret-out", str(secret_file),
        "--pair-only")


def _accounts(base: str) -> dict:
    return {row["account_id"]: row
            for row in _json_get(base, "/api/im/accounts")["accounts"]}


def _signed_inbound(base: str, account_id: str, secret: str,
                    payload: dict) -> dict:
    from kairos.im_accounts import (SIGNATURE_HEADER, TIMESTAMP_HEADER,  # noqa: E501
                                    sign_request)
    body = json.dumps(payload).encode("utf-8")
    stamp = str(int(time.time()))
    req = urllib.request.Request(
        f"{base}/api/im/{account_id}/inbound", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 TIMESTAMP_HEADER: stamp,
                 SIGNATURE_HEADER: sign_request(secret, stamp, body)})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _start_long_run(base: str, out_path: Path, *args: str) -> subprocess.Popen:
    """Run the connector in the background, output to a file (no pipe stall)."""
    _post_json(base, "/api/im/pairings", {})
    handle = out_path.open("wb")
    proc = subprocess.Popen(
        [sys.executable, str(CONNECTOR), "--base-url", base, *args],
        cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL)
    proc._out_handle = handle  # type: ignore[attr-defined]
    return proc


# ----------------------------------------------------------------------- tests

def test_the_stub_gateway_mints_a_key_only_when_the_body_is_present(
        stub_gateway, connector_module):
    """Proof 1: GenAuthKey1 needs a JSON body; the client always sends one."""
    client = connector_module.PadProClient(
        stub_gateway.url, admin_key=stub_gateway.ADMIN_KEY)
    minted = client.gen_auth_key(count=1, days=365)
    assert minted == stub_gateway.AUTH_KEY
    assert stub_gateway.auth_key_calls[-1] == {
        "key": stub_gateway.ADMIN_KEY, "body_present": True}

    # The real gateway's quirk, modelled by the stub: no body -> Code 300.
    raw = urllib.request.Request(
        f"{stub_gateway.url}/admin/GenAuthKey1?key={stub_gateway.ADMIN_KEY}",
        data=b"", method="POST")
    with urllib.request.urlopen(raw, timeout=10) as resp:
        empty = json.loads(resp.read().decode("utf-8"))
    assert empty["Code"] == 300 and "EOF" in empty["Text"]

    with pytest.raises(connector_module.GatewayError):
        connector_module.PadProClient(
            stub_gateway.url, admin_key="wrong-admin").gen_auth_key()


def test_preflight_refuses_before_any_pairing_is_claimed(
        stub_gateway, connector_module):
    """Proof 2: no key / no gateway is a refusal, not a silent half-start."""
    bare = connector_module.WeChatPadProBackend(gateway_url=stub_gateway.url)
    report = bare.preflight()
    assert report["verdict"] == "no-authorization-code"
    assert report["gateway_reachable"] is True
    with pytest.raises(connector_module.hook.BackendUnavailableError):
        bare.preflight_check()

    dead = connector_module.WeChatPadProBackend(
        gateway_url="http://127.0.0.1:1", auth_key="whatever")
    assert dead.preflight()["verdict"] == "gateway-unreachable"

    ok = connector_module.WeChatPadProBackend(
        gateway_url=stub_gateway.url, auth_key=stub_gateway.AUTH_KEY)
    assert ok.preflight()["verdict"] == "ok"


def test_pairing_through_the_gateway_creates_the_account(
        app_base, stub_gateway, secrets_dir):
    """Proof 3: gateway QR -> /pairings/*/qr -> account; secret stays on disk."""
    secret_file = secrets_dir / "padpro-pair.json"
    done = _pair_real(app_base, secret_file, "wxpad-pair", stub_gateway,
                      display_name="网关配对")
    combined = done.stdout + done.stderr
    assert done.returncode == 0, combined
    assert "bound as wxpad-pair" in combined
    # The connector really asked the gateway for a login QR and uploaded it.
    assert stub_gateway.qr_requests >= 1
    assert "uploaded the client login code" in combined

    account = _accounts(app_base)["wxpad-pair"]
    assert account["name"] == "网关配对"
    assert account["enabled"] is True
    assert "secret" not in account           # the app never echoes one

    stored = json.loads(secret_file.read_text(encoding="utf-8"))
    assert stored["account_id"] == "wxpad-pair"
    assert len(stored["secret"]) == 32
    # A secret in stdout is a leaked secret -- and so is the gateway key.
    assert stored["secret"] not in combined
    assert stub_gateway.AUTH_KEY not in combined
    assert stub_gateway.ADMIN_KEY not in combined

    from kairos.im_accounts import (SIGNATURE_HEADER, TIMESTAMP_HEADER,  # noqa: E501
                                    sign_request)
    stamp = str(int(time.time()))
    req = urllib.request.Request(
        f"{app_base}/api/im/wxpad-pair/outbound", method="GET",
        headers={TIMESTAMP_HEADER: stamp,
                 SIGNATURE_HEADER: sign_request(stored["secret"], stamp, b"")})
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert json.loads(resp.read().decode("utf-8"))["messages"] == []


def test_a_wrong_account_secret_is_refused_and_accepted_with_the_right_one(
        app_base, stub_gateway, secrets_dir, connector_module):
    """Proof 4: the account HMAC is a credential, not a formality."""
    secret_file = secrets_dir / "padpro-hmac.json"
    assert _pair_real(app_base, secret_file, "wxpad-hmac",
                      stub_gateway).returncode == 0
    secret = json.loads(secret_file.read_text(encoding="utf-8"))["secret"]
    payload = {"chat_id": "chat-hmac", "text": "hello"}

    with pytest.raises(connector_module.hook.ConnectorError) as caught:
        connector_module.hook._signed(
            app_base, "POST", "/api/im/wxpad-hmac/inbound",
            "not-the-account-secret", payload)
    assert "401" in str(caught.value)
    assert not [b for b in _json_get(app_base, "/api/im/bindings")["bindings"]
                if b["account_id"] == "wxpad-hmac"]

    accepted = _signed_inbound(app_base, "wxpad-hmac", secret, payload)
    assert accepted["ok"] is True
    assert accepted["queued_id"]


def test_a_signed_webhook_push_reaches_the_agent_and_the_reply_goes_out(
        app_base, stub_gateway, secrets_dir, fake_llm):
    """Proof 5: push -> signed inbound -> reply -> sent via gateway -> acked."""
    secret_file = secrets_dir / "padpro-loop.json"
    out_path = secrets_dir / "padpro-loop.out"
    log_file = secrets_dir / "padpro-loop.jsonl"
    callback_port = _free_port()
    cb_path = "/wxpadpro/webhook"

    assert _pair_real(app_base, secret_file, "wxpad-loop",
                      stub_gateway).returncode == 0

    proc = _start_long_run(
        app_base, out_path,
        "--backend", "real", "--gateway-url", stub_gateway.url,
        "--auth-key", stub_gateway.AUTH_KEY, "--account-id", "wxpad-loop",
        "--secret-out", str(secret_file), "--log-out", str(log_file),
        "--callback-port", str(callback_port), "--callback-path", cb_path,
        "--webhook-secret", "wh-secret-stub", "--poll-interval", "1",
        "--run-for", "45")
    try:
        _wait_until(lambda: stub_gateway.callback_url(), 40,
                    "the connector to register its callback URL")
        assert stub_gateway.callback_url().endswith(cb_path)
        assert stub_gateway.webhook_config.get("Secret") == "wh-secret-stub"
        assert stub_gateway.webhook_config.get("Enabled") is True
        _wait_until(lambda: _get(f"http://127.0.0.1:{callback_port}"
                                 f"{cb_path}")[0] == 200, 20,
                    "the connector's callback server to answer")

        calls_before = len(fake_llm.requests)
        text = "ping through the padpro webhook"
        assert stub_gateway.push_message_event("chat-padpro", text) == 200

        _wait_until(lambda: _has_direction(log_file, "in"), 40,
                    "the pushed message to reach the app")
        _wait_until(lambda: _has_direction(log_file, "out"), 40,
                    "the agent's reply to be sent back out")
        assert len(fake_llm.requests) > calls_before  # the agent really ran

        lines = _log_lines(log_file)
        inbound = [l for l in lines if l.get("direction") == "in"]
        outbound = [l for l in lines if l.get("direction") == "out"]
        assert [l["text"] for l in inbound] == [text]
        assert [l["text"] for l in outbound] == [FAKE_LLM_PREFIX + text]
        assert outbound[0]["chat_id"] == "chat-padpro"

        # The gateway really received the send, and the queue drained.
        _wait_until(lambda: any(m["Wxid"] == "chat-padpro"
                                for m in stub_gateway.sent_messages), 20,
                    "the gateway to receive SendTextMessage")
        _wait_until(lambda: _accounts(app_base)["wxpad-loop"]["pending"] == 0,
                    20, "the outbox to drain to zero")
    finally:
        _terminate_tree(proc)
        proc._out_handle.close()  # type: ignore[attr-defined]
    combined = out_path.read_text(encoding="utf-8", errors="replace")
    assert "wh-secret-stub" not in combined


def test_a_forged_webhook_signature_is_dropped(app_base, stub_gateway,
                                               secrets_dir):
    """Proof 6: a push that fails signature verification never reaches Kairos."""
    secret_file = secrets_dir / "padpro-badsig.json"
    out_path = secrets_dir / "padpro-badsig.out"
    callback_port = _free_port()

    assert _pair_real(app_base, secret_file, "wxpad-badsig",
                      stub_gateway).returncode == 0
    proc = _start_long_run(
        app_base, out_path,
        "--backend", "real", "--gateway-url", stub_gateway.url,
        "--auth-key", stub_gateway.AUTH_KEY, "--account-id", "wxpad-badsig",
        "--secret-out", str(secret_file), "--callback-port",
        str(callback_port), "--webhook-secret", "wh-secret-stub",
        "--poll-interval", "1", "--run-for", "30")
    try:
        _wait_until(lambda: stub_gateway.callback_url(), 40,
                    "the connector to register its callback URL")
        _wait_until(lambda: _get(f"http://127.0.0.1:{callback_port}"
                                 "/wxpadpro/webhook")[0] == 200, 20,
                    "the connector's callback server to answer")

        assert stub_gateway.push_message_event(
            "chat-forged", "should never arrive", signed=False) == 403

        # Give the connector time to have wrongly accepted it, if it would.
        time.sleep(3)
        assert not [b for b in _json_get(app_base, "/api/im/bindings")["bindings"]
                    if b["account_id"] == "wxpad-badsig"]
        _wait_until(lambda: "签名校验失败" in out_path.read_text(
            encoding="utf-8", errors="replace"), 15,
            "the connector to log the dropped push")
    finally:
        _terminate_tree(proc)
        proc._out_handle.close()  # type: ignore[attr-defined]


def test_a_gateway_error_does_not_crash_the_connector_and_it_retries(
        app_base, stub_gateway, secrets_dir):
    """Proof 7: send refused -> no ack, honest log, retried on the next poll."""
    secret_file = secrets_dir / "padpro-err.json"
    log_file = secrets_dir / "padpro-err.jsonl"
    assert _pair_real(app_base, secret_file, "wxpad-err",
                      stub_gateway).returncode == 0
    secret = json.loads(secret_file.read_text(encoding="utf-8"))["secret"]

    queued = _signed_inbound(app_base, "wxpad-err", secret,
                             {"chat_id": "chat-err", "text": "queue me"})
    assert queued["queued_id"]
    assert _accounts(app_base)["wxpad-err"]["pending"] >= 1

    stub_gateway.fail_sends = True
    failed = _run_connector(
        app_base, "--backend", "real", "--gateway-url", stub_gateway.url,
        "--auth-key", stub_gateway.AUTH_KEY, "--account-id", "wxpad-err",
        "--secret-out", str(secret_file), "--log-out", str(log_file),
        "--poll-once")
    combined = failed.stdout + failed.stderr
    assert failed.returncode == 0, combined          # no crash
    assert "[out] client refused" in combined         # honest record
    assert "模拟发送失败" in combined
    assert _accounts(app_base)["wxpad-err"]["pending"] >= 1   # NOT acked
    assert not _log_lines(log_file)                  # nothing was sent

    stub_gateway.fail_sends = False
    retried = _run_connector(
        app_base, "--backend", "real", "--gateway-url", stub_gateway.url,
        "--auth-key", stub_gateway.AUTH_KEY, "--account-id", "wxpad-err",
        "--secret-out", str(secret_file), "--log-out", str(log_file),
        "--poll-once")
    assert retried.returncode == 0, retried.stdout + retried.stderr
    assert [l["text"] for l in _log_lines(log_file)
            if l.get("direction") == "out"] == [FAKE_LLM_PREFIX + "queue me"]
    assert stub_gateway.sent_messages[-1]["Wxid"] == "chat-err"
    assert _accounts(app_base)["wxpad-err"]["pending"] == 0   # acked


def test_the_stub_backend_runs_the_protocol_path_with_no_gateway(
        app_base, secrets_dir):
    """Proof 8: --backend stub still moves a message end to end."""
    secret_file = secrets_dir / "padpro-stub.json"
    log_file = secrets_dir / "padpro-stub.jsonl"
    paired = _pair_stub(app_base, secret_file, "wxpad-stubbe")
    assert paired.returncode == 0, paired.stdout + paired.stderr
    assert "bound as wxpad-stubbe" in paired.stdout + paired.stderr

    secret = json.loads(secret_file.read_text(encoding="utf-8"))["secret"]
    queued = _signed_inbound(app_base, "wxpad-stubbe", secret,
                             {"chat_id": "chat-stub", "text": "stub me"})
    assert queued["queued_id"]

    done = _run_connector(
        app_base, "--backend", "stub", "--account-id", "wxpad-stubbe",
        "--secret-out", str(secret_file), "--log-out", str(log_file),
        "--poll-once")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [l["text"] for l in _log_lines(log_file)
            if l.get("direction") == "out"] == [FAKE_LLM_PREFIX + "stub me"]
    assert _accounts(app_base)["wxpad-stubbe"]["pending"] == 0
