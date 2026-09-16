"""Does a custom base URL actually take effect? Three rings, no guessing.

The question "is my custom base URL really used?" has three separate answers, and
a setting that looks right in the drawer can fail at any of them:

  1. STORED      the value survives a save and a reload from disk
  2. RESOLVED    resolve_base_url() picks endpointUrl over a stale baseUrl, and
                 strips the path suffix the SDK expects to append itself
  3. SENT        the provider actually issues its request to that host

Rings 1 and 2 are pure; ring 3 needs something listening, so this script starts
a local stub on 127.0.0.1 and asserts on what the stub received. Nothing leaves
the machine, no real key is used, and the user's data directory is never touched
(SettingsStore takes an explicit path).

    python scripts/verify_custom_base_url.py
"""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REQUESTS: list[tuple[str, str, dict]] = []
LOCK = threading.Lock()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stub(BaseHTTPRequestHandler):
    """Answers like the two APIs, and remembers what it was asked."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # keep the output readable
        return

    def _record(self) -> None:
        with LOCK:
            REQUESTS.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}))

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        self._record()
        if self.path.rstrip("/").endswith("/models"):
            if "x-api-key" in {k.lower() for k in self.headers}:
                self._send({"data": [
                    {"id": "claude-sonnet-4-5-20250929", "display_name": "Claude Sonnet 4.5",
                     "type": "model"},
                    {"id": "claude-opus-4-1-20250805", "display_name": "Claude Opus 4.1",
                     "type": "model"},
                ]})
            else:
                self._send({"object": "list", "data": [
                    {"id": "stub-model-small", "object": "model"},
                    {"id": "stub-model-large", "object": "model"},
                ]})
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        self._record()
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.rstrip("/").endswith("/v1/messages"):  # Anthropic shape
            self._send({
                "id": "msg_stub", "type": "message", "role": "assistant",
                "model": "stub", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "stub-ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
            return
        # OpenAI shape
        self._send({
            "id": "chatcmpl-stub", "object": "chat.completion", "created": 0,
            "model": "stub", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "stub-ok"},
                 "finish_reason": "stop"},
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


def start_stub(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def paths() -> list[str]:
    with LOCK:
        return [p for _m, p, _h in REQUESTS]


def headers_for(path_suffix: str) -> dict:
    with LOCK:
        for _m, p, h in REQUESTS:
            if p.rstrip("/").endswith(path_suffix):
                return h
    return {}


def ring1_stored(port: int, tmp: Path) -> tuple[bool, str]:
    """A custom endpointUrl survives the settings round-trip and reaches disk."""
    from kairos.settings_store import SettingsStore

    store = SettingsStore(path=tmp / "settings.json")
    custom = f"http://127.0.0.1:{port}/v1/chat/completions"
    store.update({"provider": {"openai": {
        "endpointUrl": custom, "baseUrl": "https://api.deepseek.com/v1",
        "apiKey": "stub-key", "model": "stub-model-large",
    }, "active": "openai"}})
    back = store.get()
    # The dataclass keeps the per-provider configs flat (provider_openai); the
    # file may hold them nested under "provider" (the wire shape the drawer
    # posts) or flat. Both are legitimate, so accept either — the point is that
    # the value reaches disk unchanged.
    on_disk = json.loads((tmp / "settings.json").read_text(encoding="utf-8"))
    nested = (on_disk.get("provider") or {}).get("openai") or {}
    flat = on_disk.get("provider_openai") or {}
    disk_value = nested.get("endpointUrl") or flat.get("endpointUrl")
    dataclass_value = back.provider_openai.endpointUrl
    ok = disk_value == custom and dataclass_value == custom
    return ok, (
        f"dataclass={dataclass_value}  disk={disk_value}  "
        f"（文件键: {'provider.openai' if nested else 'provider_openai'}）"
    )


def ring2_resolved(port: int) -> tuple[bool, str]:
    """endpointUrl wins, and each protocol's base is what its client expects."""
    from kairos.llm.endpoints import resolve_anthropic_base, resolve_base_url

    openai_cfg = {
        "endpointUrl": f"http://127.0.0.1:{port}/v1/chat/completions",
        "baseUrl": "https://api.deepseek.com/v1",       # a stale value on purpose
    }
    got = resolve_base_url(openai_cfg, default="https://api.openai.com/v1",
                           suffix="/chat/completions")
    want = f"http://127.0.0.1:{port}/v1"
    # Anthropic: use the app's own resolver, not a suffix I picked here. Whatever
    # it returns must be a base the provider can append "/v1/messages" to
    # exactly once — that concatenation is what goes on the wire.
    got_a = resolve_anthropic_base({"endpointUrl": f"http://127.0.0.1:{port}/v1/messages"})
    want_a = f"http://127.0.0.1:{port}"
    ok = (got == want) and (got_a == want_a) and f"{got_a}/v1/messages".count("/v1/") == 1
    return ok, f"openai-base={got} → {got}/chat/completions · anthropic-base={got_a} → {got_a}/v1/messages"


async def ring3_sent(port: int) -> tuple[bool, str]:
    """The provider really posts to the custom host.

    Built through the app's own factory — create_provider() is what the model
    router calls, so this exercises the real choice of client rather than one I
    picked. (litellm is optional and not installed in every environment.)
    """
    from kairos.llm.base import LLMConfig, LLMMessage
    from kairos.llm.provider_registry import create_provider

    cfg = LLMConfig(provider="openai", model="stub-model-large", api_key="stub-key",
                    base_url=f"http://127.0.0.1:{port}/v1")
    provider = create_provider(cfg)
    try:
        reply = await provider.complete([LLMMessage(role="user", content="ping")])
        text = getattr(reply, "content", "") or ""
        err = ""
    except Exception as exc:
        text, err = "", f"{type(exc).__name__}: {exc}"
    finally:
        close = getattr(provider, "close", None)
        if close:
            await close()
    arrived = any(p.rstrip("/").endswith("/chat/completions") for p in paths())
    detail = f"client={type(provider).__name__}  reply={text!r}  stub saw {paths()}"
    if err:
        detail += f"  err={err}"
    return (arrived and not err), detail


async def ring3b_anthropic(port: int) -> tuple[bool, str]:
    """Same for the Anthropic path — where the double /v1 used to show up."""
    from kairos.llm.base import LLMConfig, LLMMessage
    from kairos.llm.endpoints import resolve_anthropic_base
    from kairos.llm.providers.anthropic_provider import AnthropicProvider

    drawer_value = f"http://127.0.0.1:{port}/v1/messages"
    base = resolve_anthropic_base({"endpointUrl": drawer_value})
    provider = AnthropicProvider(LLMConfig(provider="anthropic", model="stub",
                                           api_key="stub-key", base_url=base))
    try:
        reply = await provider.complete([LLMMessage(role="user", content="ping")])
        text = getattr(reply, "content", "") or ""
        err = ""
    except Exception as exc:  # a 404 from the doubled path lands here
        text, err = "", f"{type(exc).__name__}: {exc}"
    finally:
        close = getattr(provider, "close", None)
        if close:
            await close()
    hit = any(p.rstrip("/").endswith("/v1/messages") for p in paths())
    doubled = any("/v1/v1/" in p for p in paths())
    detail = f"base={base}  reply={text!r}" + (f"  err={err}" if err else "")
    if doubled:
        detail += "  ← 双 /v1：服务端收到 /v1/v1/messages"
    return hit and not doubled and not err, detail


def main() -> int:
    port = _free_port()
    server = start_stub(port)
    print(f"  本机桩服务: http://127.0.0.1:{port}（不出网、不用真 key、不碰你的 data/）\n")
    results: list[tuple[str, bool, str]] = []

    def guard(name: str, fn, *args) -> None:
        """Run one ring; a crash is a failed ring, not a lost report."""
        try:
            ok, detail = fn(*args)
        except Exception as exc:
            ok, detail = False, f"抛出异常 {type(exc).__name__}: {exc}"
        results.append((name, ok, detail))

    with tempfile.TemporaryDirectory(prefix="kairos_verify_") as td:
        guard("① 存得进（settings 往返 + 落盘）", ring1_stored, port, Path(td))
    guard("② 取得对（endpointUrl 优先 + 剥后缀）", ring2_resolved, port)
    guard("③ 真发得到（OpenAI 兼容）", lambda p: asyncio.run(ring3_sent(p)), port)
    guard("③ 真发得到（Anthropic 协议）", lambda p: asyncio.run(ring3b_anthropic(p)), port)
    server.shutdown()

    print("  结果:")
    width = max(len(name) for name, _ok, _d in results)
    for name, ok, detail in results:
        print(f"    {'✓ PASS' if ok else '✗ FAIL'}  {name.ljust(width)}  {detail}")
    failed = [n for n, ok, _d in results if not ok]
    print(f"\n  {len(results) - len(failed)}/{len(results)} 通过" + (f"，失败: {failed}" if failed else " ✓"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
