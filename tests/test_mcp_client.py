"""Tests for the MCP (Model Context Protocol) client.

We stand up a small in-process MCP server (a Python script that
speaks JSON-RPC over stdio) and verify the client can:
  - complete the initialize handshake
  - list tools
  - call a tool and unpack the result
  - handle errors gracefully
  - load configs from project + user YAML
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.mcp_client import (
    DEFAULT_REQUEST_TIMEOUT_S,
    McpError,
    McpRegistry,
    McpServerConfig,
    StdioMcpClient,
    TEMPLATE_YAML,
    _deep_merge,
    load_configs,
)


# ---------------------------------------------------------------------------
# Pure config tests (no subprocess)
# ---------------------------------------------------------------------------


def test_deep_merge_dicts_recurse():
    assert _deep_merge({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}


def test_a_fresh_install_gets_the_shipped_defaults(monkeypatch):
    """No config anywhere is no longer "nothing".

    R38.12: the servers Kairos serves itself (filesystem/git/sqlite/time/fetch)
    apply until someone overrides them, so a brand-new install has working MCP
    tools. Asking for the user's own config alone still returns nothing.
    """
    monkeypatch.delenv("KAIROS_NO_BUNDLED_MCP", raising=False)
    with tempfile.TemporaryDirectory() as d:
        out = load_configs(project_dir=Path(d), user_dir=Path(d) / "missing")
        assert out, "a fresh install should already have the bundled servers"
        assert all(cfg.source == "bundled-plugin" for cfg in out.values())
        assert load_configs(project_dir=Path(d), user_dir=Path(d) / "missing",
                            include_bundled=False) == {}


def test_load_configs_project_wins_on_collision():
    with tempfile.TemporaryDirectory() as d:
        user_dir = Path(d) / "user"
        proj_dir = Path(d) / "proj"
        user_dir.mkdir()
        proj_dir.mkdir()
        # Use absolute paths so the loader doesn't refuse to load
        # the test fixture (which obviously isn't on PATH).
        user_cmd = str(Path(sys.executable).resolve())
        proj_cmd = str((Path(d) / "fake-proj-cmd.exe").resolve())
        (user_dir / "mcp.yaml").write_text(
            f"mcp_servers:\n  my_server:\n    command: {user_cmd!r}\n    args: [a]\n",
            encoding="utf-8",
        )
        (proj_dir / ".kairos").mkdir()
        (proj_dir / ".kairos" / "mcp.yaml").write_text(
            f"mcp_servers:\n  my_server:\n    command: {proj_cmd!r}\n    args: [b, c]\n",
            encoding="utf-8",
        )
        out = load_configs(project_dir=proj_dir, user_dir=user_dir)
        cfg = out["my_server"]
        # Project wins on the `command` field. Compare via Path so
        # backslash-vs-double-backslash differences don't trip the
        # test on Windows.
        assert Path(cfg.command) == Path(proj_cmd)
        assert cfg.args == ["b", "c"]


def test_load_configs_skips_missing_command():
    with tempfile.TemporaryDirectory() as d:
        proj_dir = Path(d)
        (proj_dir / ".kairos").mkdir()
        (proj_dir / ".kairos" / "mcp.yaml").write_text(
            "mcp_servers:\n  no_cmd:\n    args: [a]\n",
            encoding="utf-8",
        )
        out = load_configs(project_dir=proj_dir, user_dir=Path(d) / "x",
                           include_bundled=False)
        assert out == {}


def test_load_configs_env_refs_substituted():
    with tempfile.TemporaryDirectory() as d:
        proj_dir = Path(d)
        (proj_dir / ".kairos").mkdir()
        # Use an absolute path so the loader doesn't refuse the
        # "command: foo" fixture below.
        abs_cmd = str(Path(sys.executable).resolve())
        os.environ["KAIROS_TEST_ENV"] = "expanded_value"
        try:
            (proj_dir / ".kairos" / "mcp.yaml").write_text(
                f"mcp_servers:\n  s:\n    command: {abs_cmd!r}\n    env:\n"
                "      X: \"${KAIROS_TEST_ENV}\"\n",
                encoding="utf-8",
            )
            out = load_configs(project_dir=proj_dir, user_dir=Path(d) / "x")
            cfg = out["s"]
            # The raw config preserves the literal reference; the
            # expansion happens at spawn time so the subprocess
            # gets the real value.
            assert cfg.env.get("X") == "${KAIROS_TEST_ENV}"
            assert cfg.expanded_env().get("X") == "expanded_value"
        finally:
            os.environ.pop("KAIROS_TEST_ENV", None)


def test_template_yaml_contains_examples():
    assert "mcp_servers:" in TEMPLATE_YAML
    assert "npx" in TEMPLATE_YAML
    assert "${GITHUB_TOKEN}" in TEMPLATE_YAML


# ---------------------------------------------------------------------------
# Live subprocess test (skipped if Python is not available)
# ---------------------------------------------------------------------------


# A tiny in-process MCP server that responds to a few well-known
# methods. We invoke it via `python -m` so the test doesn't need a
# real MCP server on disk.
#
# We use synchronous stdin/stdout (readline / print) instead of
# asyncio StreamReader because on Windows the latter doesn't
# integrate cleanly with the anonymous pipes that
# `asyncio.create_subprocess_exec(..., stdin=PIPE, stdout=PIPE)`
# creates.
MOCK_SERVER_SCRIPT = r"""
import sys
import json


def respond(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method")
        if method == "initialize":
            respond({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "mock", "version": "0.0.1"},
                    "capabilities": {"tools": {"listChanged": False}},
                }
            })
        elif method == "notifications/initialized":
            continue  # notification, no response
        elif method == "tools/list":
            respond({
                "jsonrpc": "2.0", "id": rid,
                "result": {"tools": [
                    {"name": "echo", "description": "Echoes the input",
                     "inputSchema": {"type": "object", "properties":
                         {"text": {"type": "string"}}, "required": ["text"]}},
                    {"name": "fail", "description": "Always errors",
                     "inputSchema": {"type": "object", "properties": {}}},
                ]}
            })
        elif method == "tools/call":
            params = req.get("params") or {}
            if params.get("name") == "echo":
                txt = (params.get("arguments") or {}).get("text", "")
                respond({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text",
                                             "text": "echo: " + txt}],
                               "isError": False}
                })
            else:
                respond({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": "boom"}],
                               "isError": True}
                })
        else:
            respond({
                "jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601,
                          "message": "Method not found: " + str(method)}
            })


if __name__ == "__main__":
    main()
"""


def _has_python() -> bool:
    return shutil.which(sys.executable) is not None


@pytest.mark.asyncio
async def test_client_full_handshake_and_tool_call():
    if not _has_python():
        pytest.skip("python interpreter not available")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(MOCK_SERVER_SCRIPT)
        script_path = f.name
    try:
        cfg = McpServerConfig(
            name="mock", command=sys.executable, args=[script_path]
        )
        client = StdioMcpClient(cfg, request_timeout_s=10.0)
        try:
            info = await client.start()
            assert info["serverInfo"]["name"] == "mock"
            tools = await client.list_tools()
            assert {t["name"] for t in tools} == {"echo", "fail"}

            result = await client.call_tool(
                "echo", {"text": "hello"}
            )
            assert result["isError"] is False
            assert result["content"][0]["text"] == "echo: hello"

            result2 = await client.call_tool("fail", {})
            assert result2["isError"] is True
            assert result2["content"][0]["text"] == "boom"
        finally:
            await client.close()
    finally:
        os.unlink(script_path)


@pytest.mark.asyncio
async def test_client_close_is_idempotent():
    if not _has_python():
        pytest.skip("python interpreter not available")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(MOCK_SERVER_SCRIPT)
        script_path = f.name
    try:
        cfg = McpServerConfig(
            name="mock", command=sys.executable, args=[script_path]
        )
        client = StdioMcpClient(cfg, request_timeout_s=5.0)
        await client.start()
        await client.close()
        # Idempotent: should not raise.
        await client.close()
    finally:
        os.unlink(script_path)


@pytest.mark.asyncio
async def test_client_missing_command_raises():
    cfg = McpServerConfig(
        name="absent", command="/no/such/binary/please"
    )
    client = StdioMcpClient(cfg, request_timeout_s=5.0)
    with pytest.raises(McpError):
        await client.start()
    await client.close()


# ---------------------------------------------------------------------------
# Registry: integration test that runs a single mock server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_loads_and_aggregates_tools():
    if not _has_python():
        pytest.skip("python interpreter not available")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(MOCK_SERVER_SCRIPT)
        script_path = f.name
    try:
        # Bypass shutil.which() for the mock server by giving an
        # absolute path; we know sys.executable exists.
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / ".kairos").mkdir()
            # Use forward slashes in YAML; Windows backslashes would
            # need escaping and we want this to be readable too.
            exe = sys.executable.replace("\\", "/")
            sp = script_path.replace("\\", "/")
            (proj / ".kairos" / "mcp.yaml").write_text(
                f"mcp_servers:\n  mock:\n"
                f"    command: {exe}\n"
                f"    args: [\"{sp}\"]\n",
                encoding="utf-8",
            )
            registry = McpRegistry()
            # This test configures the servers it asserts on, so the shipped
            # defaults are switched off.
            registry.include_bundled = False
            registry.load(project_dir=proj)
            await registry.start_all()
            try:
                tools = registry.all_tools()
                # Two MCP tools, namespaced.
                names = sorted(t.name for t in tools)
                assert names == ["mcp_mock__echo", "mcp_mock__fail"], names
                # Sanity: the adapter's `to_schema()` is a valid
                # OpenAI-style function dict.
                echo = registry.get_tool("mcp_mock__echo")
                assert echo is not None
                schema = echo.to_schema()
                assert schema["name"] == "mcp_mock__echo"
                assert "parameters" in schema
                # We can actually execute via the adapter.
                result = await echo.execute(text="hi")
                assert result.success is True, f"output={result.output!r} error={result.error!r}"
                assert "echo: hi" in result.output
            finally:
                await registry.close_all()
    finally:
        os.unlink(script_path)


@pytest.mark.asyncio
async def test_registry_handles_failing_server():
    """A server that fails to start should be recorded in
    startup_errors but not crash the whole registry."""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / ".kairos").mkdir()
        (proj / ".kairos" / "mcp.yaml").write_text(
            "mcp_servers:\n  bad:\n    command: /definitely/missing/binary\n",
            encoding="utf-8",
        )
        registry = McpRegistry()
        # This test configures the servers it asserts on, so the shipped
        # defaults are switched off.
        registry.include_bundled = False
        registry.load(project_dir=proj)
        await registry.start_all()
        try:
            assert registry.startup_errors == {"bad": "..."} or "bad" in registry.startup_errors
            assert registry.all_tools() == []
        finally:
            await registry.close_all()
