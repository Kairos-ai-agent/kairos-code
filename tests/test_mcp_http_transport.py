"""R38.12 — MCP over HTTP, tested against a real server.

A client that "supports HTTP" but was only ever pointed at a mock is not
support. So this spins up an actual Streamable-HTTP MCP server in-process (the
SDK's manager, serving the bundled ``time`` server), connects the real
``HttpMcpClient`` over a real socket, and calls a real tool. Offline: everything
is on 127.0.0.1.

Skipped when the optional MCP stack or uvicorn/starlette is unavailable.
"""

import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest

from kairos.mcp_client import (HttpMcpClient, McpServerConfig, StdioMcpClient,
                               client_for, load_configs)

pytest.importorskip("mcp")
pytest.importorskip("uvicorn")
pytest.importorskip("starlette")


# ---------------------------------------------------------------------------
# config parsing / transport selection (no network)
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, body: str) -> Path:
    user = tmp_path / "user"
    user.mkdir(parents=True, exist_ok=True)
    (user / "mcp.yaml").write_text(body, encoding="utf-8")
    return user


def test_a_http_server_needs_a_url_and_no_command(tmp_path):
    user = _write_config(tmp_path, """
mcp_servers:
  remote:
    transport: http
    url: https://example.invalid/mcp
    headers:
      Authorization: "Bearer ${KAIROS_TEST_TOKEN}"
""")
    configs = load_configs(user_dir=user)
    cfg = configs["remote"]
    assert cfg.transport == "http"
    assert cfg.url == "https://example.invalid/mcp"
    # The command is empty on purpose — nothing is spawned.
    assert cfg.command == ""
    assert cfg.expanded_headers()["Authorization"].startswith("Bearer ")


def test_a_remote_server_without_a_url_is_skipped_not_assumed(tmp_path):
    user = _write_config(tmp_path, """
mcp_servers:
  broken:
    transport: http
  good:
    transport: sse
    url: https://example.invalid/sse
""")
    configs = load_configs(user_dir=user)
    assert "broken" not in configs
    assert configs["good"].transport == "sse"


def test_an_unknown_transport_is_skipped(tmp_path):
    user = _write_config(tmp_path, """
mcp_servers:
  weird:
    transport: carrier-pigeon
    command: echo
""")
    assert "weird" not in load_configs(user_dir=user)


def test_the_client_matches_the_transport(tmp_path):
    stdio = McpServerConfig(name="s", command="echo")
    http = McpServerConfig(name="h", transport="http", url="http://x/mcp")
    sse = McpServerConfig(name="e", transport="sse", url="http://x/sse")
    assert isinstance(client_for(stdio), StdioMcpClient)
    assert isinstance(client_for(http), HttpMcpClient)
    assert isinstance(client_for(sse), HttpMcpClient)


def test_a_missing_url_fails_loudly_on_start():
    client = HttpMcpClient(McpServerConfig(name="x", transport="http"))
    with pytest.raises(Exception) as exc:
        asyncio.run(client.start())
    assert "url" in str(exc.value)


# ---------------------------------------------------------------------------
# the real round trip
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _HttpServer:
    """A Streamable-HTTP MCP server (the bundled `time` server) on a thread."""

    def __init__(self) -> None:
        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Mount

        from kairos.mcp_local_servers import build_mcp_server

        self._server_obj, self._tool_names = build_mcp_server("time")
        self._manager = StreamableHTTPSessionManager(
            app=self._server_obj, json_response=True, stateless=True)
        manager = self._manager

        import contextlib

        @contextlib.asynccontextmanager
        async def lifespan(_app):
            async with manager.run():
                yield

        asgi = Starlette(
            routes=[Mount("/mcp", app=manager.handle_request)],
            lifespan=lifespan,
        )
        self.port = _free_port()
        config = uvicorn.Config(asgi, host="127.0.0.1", port=self.port,
                                log_level="error", lifespan="on")
        self._uvicorn = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._uvicorn.run, daemon=True)

    def __enter__(self) -> "_HttpServer":
        self._thread.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            if getattr(self._uvicorn, "started", False):
                return self
            time.sleep(0.05)
        raise RuntimeError("the test MCP server did not start")

    def __exit__(self, *exc) -> None:
        self._uvicorn.should_exit = True
        self._thread.join(timeout=10)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@pytest.mark.timeout(120)
def test_a_real_client_talks_to_a_real_http_server():
    with _HttpServer() as server:
        cfg = McpServerConfig(name="time", transport="http", url=server.url)

        async def round_trip():
            async with HttpMcpClient(cfg) as client:
                info = client.server_info()
                tools = await client.list_tools()
                result = await client.call_tool("time_now", {})
                return info, tools, result

        info, tools, result = asyncio.run(round_trip())

    assert info, "initialize returned nothing"
    assert info.get("serverInfo", {}).get("name"), info

    names = {t["name"] for t in tools}
    assert names == set(server._tool_names), names
    # The camelCase field the tool adapter reads must survive the SDK models.
    assert all("inputSchema" in t for t in tools), tools[0].keys()

    # And a real call really returns a real answer.
    text = "".join(part.get("text", "")
                   for part in (result.get("content") or []))
    assert '"unix"' in text, result
