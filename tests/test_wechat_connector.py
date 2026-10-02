"""End-to-end test for the local WeChat connector's protocol path.

This drives the **real app** -- launched the way ``scripts/smoke_binary.py``
launches it, with an isolated ``LOCALAPPDATA`` and ``--port 9555`` -- and the
**real connector** at ``connectors/wechat_hook.py`` in its ``sim`` backend, so
no WeChat client, no login and no hook are involved. What is proven:

1. a pairing claimed off ``/api/im/pairings/pending`` ends with an account,
   and the account secret lands in the ``--secret-out`` file and nowhere else;
2. a wrong pairing secret is refused, and no account is created by it;
3. a message the app queues for that account is actually sent by the connector
   and then acknowledged -- the outbox drains to zero;
4. a message the fake backend *receives* reaches the app, becomes a
   conversation with its own project, and the agent's reply comes back out
   through the connector.

The app is given a fake OpenAI-compatible endpoint (a local HTTP server in
this process) through the settings file the desktop launcher reads, because
the point of the test is the connector protocol, not the model. Everything
else -- the Coder, the outbound queue, the signing -- is the real thing.

``connectors/`` is deliberately not in the public tree (it carries login
state and injects into one client build), so this module skips with an
explicit reason when the connector is not present.
"""
from __future__ import annotations

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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONNECTOR = REPO / "connectors" / "wechat_hook.py"
APP_PORT = 9555
FAKE_LLM_PREFIX = "FAKE-LLM-REPLY: "

pytestmark = pytest.mark.skipif(
    not CONNECTOR.is_file(),
    reason=(f"{CONNECTOR} is absent. The WeChat connector is a local-only "
            "component (see the repository .gitignore and connectors/README.md); "
            "this suite can only run on a machine that has it."))


# --------------------------------------------------------------------- pieces

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
                self.send_header("Content-Length", str(len(data := payload)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Stop the app *and* anything it forked (a launcher may have)."""
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


# -------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def fake_llm() -> _FakeLLM:
    server = _FakeLLM()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def app_base(tmp_path_factory, fake_llm):
    """The real app, isolated, on --port 9555, with a fake model behind it.

    ``LOCALAPPDATA`` is the isolation lever: ``kairos_code_launcher.py`` pins
    its data directory to ``%LOCALAPPDATA%/kairos-code``, so pointing it at a
    throwaway directory gives the test a database, a workspace root and a
    settings file that the developer's own install never sees.
    """
    local_appdata = tmp_path_factory.mktemp("localappdata-wxconn")
    data_dir = local_appdata / "kairos-code" / "data"
    data_dir.mkdir(parents=True)
    (local_appdata / "kairos-code" / "logs").mkdir(parents=True)
    # The desktop Settings drawer's shape: the active provider is openai and
    # its endpoint is our local fake. resolve_base_url() strips the
    # /chat/completions suffix, so this is the URL the SDK will call.
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
    env.pop("KAIROS_DATA_DIR", None)  # the launcher pins its own
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
        # The launcher falls back to a free port if 9555 is taken. That would
        # mean this suite is not testing what it says it tests, so it fails
        # loudly instead of quietly running somewhere else.
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


@pytest.fixture(scope="module")
def connector_module():
    """The connector as a module, for its own pairing helpers."""
    spec = importlib.util.spec_from_file_location("wechat_connector",
                                                  CONNECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def secrets_dir(tmp_path) -> Path:
    return tmp_path / "secrets"


def _run_connector(base: str, *args: str, timeout: float = 180.0,
                   cwd: Path = None) -> subprocess.CompletedProcess:
    """Start a pairing on the app, then run the connector over it.

    The pairing is created first because that is the operator's order: click
    'connect', then start the connector (which claims it).
    """
    _post_json(base, "/api/im/pairings", {})
    cmd = [sys.executable, str(CONNECTOR), "--base", base, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=str(cwd or REPO), encoding="utf-8",
                          errors="replace")


def _pair(base: str, secret_file: Path, account_id: str,
          display_name: str = "测试微信") -> subprocess.CompletedProcess:
    return _run_connector(
        base, "--backend", "sim", "--account-id", account_id,
        "--display-name", display_name, "--secret-out", str(secret_file),
        "--pair-only")


def _accounts(base: str) -> dict:
    return {row["account_id"]: row
            for row in _json_get(base, "/api/im/accounts")["accounts"]}


def _signed_get_outbound(base: str, account_id: str, secret: str) -> dict:
    from kairos.im_accounts import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign_request  # noqa: E501
    body = b""
    stamp = str(int(time.time()))
    req = urllib.request.Request(
        f"{base}/api/im/{account_id}/outbound", method="GET",
        headers={TIMESTAMP_HEADER: stamp,
                 SIGNATURE_HEADER: sign_request(secret, stamp, body)})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _signed_inbound(base: str, account_id: str, secret: str,
                    payload: dict) -> dict:
    from kairos.im_accounts import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign_request  # noqa: E501
    body = json.dumps(payload).encode("utf-8")
    stamp = str(int(time.time()))
    req = urllib.request.Request(
        f"{base}/api/im/{account_id}/inbound", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 TIMESTAMP_HEADER: stamp,
                 SIGNATURE_HEADER: sign_request(secret, stamp, body)})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _log_lines(path: Path) -> list:
    return [json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line]


# ----------------------------------------------------------------------- tests

def test_a_pairing_becomes_an_account_and_the_secret_only_lands_in_the_file(
        app_base, secrets_dir):
    """Proof 1: /pairings/pending -> /qr -> /scanned -> /confirm -> account."""
    secret_file = secrets_dir / "wx-pair.json"
    done = _pair(app_base, secret_file, "wx-pair", "配对微信")
    combined = done.stdout + done.stderr
    assert done.returncode == 0, combined
    assert "bound as wx-pair" in combined

    account = _accounts(app_base)["wx-pair"]
    assert account["name"] == "配对微信"
    assert account["enabled"] is True
    assert "secret" not in account          # the app never echoes one either

    stored = json.loads(secret_file.read_text(encoding="utf-8"))
    assert stored["account_id"] == "wx-pair"
    assert len(stored["secret"]) == 32
    # The one rule that matters: a secret in stdout is a leaked secret.
    assert stored["secret"] not in combined
    # And it is the real one: it signs a request the app accepts.
    assert _signed_get_outbound(app_base, "wx-pair", stored["secret"])


def test_a_wrong_pairing_secret_is_refused_and_creates_nothing(
        app_base, connector_module):
    """Proof 2: the pairing secret is a credential, not a formality."""
    pairing = _post_json(app_base, "/api/im/pairings", {})
    claimed = connector_module.claim_pairing(app_base)
    assert claimed and claimed["pairing_id"] == pairing["pairing_id"]

    with pytest.raises(connector_module.PairingError) as caught:
        connector_module.confirm_pairing(
            app_base, claimed["pairing_id"], "not-the-pairing-secret",
            "wx-intruder", "intruder")
    assert caught.value.status == 403
    assert "wx-intruder" not in _accounts(app_base)

    # The same call with the real secret works, so the 403 was the secret.
    ok = connector_module.confirm_pairing(
        app_base, claimed["pairing_id"], claimed["secret"], "wx-legit",
        "legit")
    assert ok["account_id"] == "wx-legit"
    assert ok["secret"]


def test_a_message_the_app_queues_is_sent_by_the_connector_and_acked(
        app_base, secrets_dir):
    """Proof 3: the outbox is the only delivery path, and it drains."""
    secret_file = secrets_dir / "wx-out.json"
    log_file = secrets_dir / "wx-out.jsonl"
    assert _pair(app_base, secret_file, "wx-out").returncode == 0
    secret = json.loads(secret_file.read_text(encoding="utf-8"))["secret"]

    # Queue a reply the way the app does: an inbound message the agent answers.
    queued = _signed_inbound(app_base, "wx-out", secret,
                             {"chat_id": "chat-out", "text": "queue me",
                              "chat_name": "排队会话"})
    assert queued["queued_id"]
    assert _accounts(app_base)["wx-out"]["pending"] >= 1

    # The connector's own CLI, one poll: send, then ack.
    done = _run_connector(
        app_base, "--backend", "sim", "--account-id", "wx-out",
        "--secret-out", str(secret_file), "--log-out", str(log_file),
        "--poll-once")
    assert done.returncode == 0, done.stdout + done.stderr

    sent = [line for line in _log_lines(log_file)
            if line.get("direction") == "out"]
    assert [line["text"] for line in sent] == [FAKE_LLM_PREFIX + "queue me"]
    assert sent[0]["chat_id"] == "chat-out"
    # Acked, so nothing is left for a second connector to re-send.
    assert _accounts(app_base)["wx-out"]["pending"] == 0
    # The secret never reached the trace file either.
    assert secret not in log_file.read_text(encoding="utf-8")


def test_an_inbound_message_from_the_backend_reaches_the_agent_and_comes_back(
        app_base, secrets_dir, fake_llm):
    """Proof 4: fake client -> signed inbound -> project -> reply -> sent."""
    secret_file = secrets_dir / "wx-in.json"
    log_file = secrets_dir / "wx-in.jsonl"
    assert _pair(app_base, secret_file, "wx-in", "接收微信").returncode == 0

    calls_before = len(fake_llm.requests)
    text = "ping from the fake client"
    # No --poll-interval here on purpose: the shipped default (1.5s) is what
    # the loop is specified to poll at.
    done = _run_connector(
        app_base, "--backend", "sim", "--account-id", "wx-in",
        "--secret-out", str(secret_file), "--log-out", str(log_file),
        "--inbound-message", text, "--inbound-chat-id", "chat-in",
        "--inbound-chat-name", "接收会话", "--inbound-delay", "1",
        "--run-for", "25")
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(fake_llm.requests) > calls_before  # the agent really ran

    lines = _log_lines(log_file)
    inbound = [line for line in lines if line.get("direction") == "in"]
    outbound = [line for line in lines if line.get("direction") == "out"]
    assert [line["text"] for line in inbound] == [text]
    # The reply is the agent's, produced by the app, not invented connector-side.
    assert [line["text"] for line in outbound] == [FAKE_LLM_PREFIX + text]
    assert outbound[0]["chat_id"] == "chat-in"

    # The conversation became its own project, bound per (account, chat).
    bindings = [b for b in _json_get(app_base, "/api/im/bindings")
                ["bindings"] if b["account_id"] == "wx-in"]
    assert len(bindings) == 1
    assert bindings[0]["chat_id"] == "chat-in"
    assert bindings[0]["project_id"]
    projects = {p["id"] for p in _json_get(app_base, "/api/projects")["projects"]}
    assert bindings[0]["project_id"] in projects
    assert _accounts(app_base)["wx-in"]["pending"] == 0
