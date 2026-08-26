"""End-to-end tests for the built-in MCP filesystem server.

These tests spawn the real server subprocess and connect via the existing
``StdioMcpClient`` — no mocking of either side.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from kairos.mcp_client import McpServerConfig, StdioMcpClient
from kairos.mcp_filesystem_server import (
    FsSandbox,
    McpFilesystemFactory,
    PathSecurityError,
    _MAX_READ_BYTES,
    _TOOLS,
    build_mcp_server,
    tool_list_directory,
    tool_read_file,
    tool_search_files,
    tool_stat,
    tool_write_file,
)


# ---------------------------------------------------------------------------
# Pure-function sandbox / tool tests (no subprocess)
# ---------------------------------------------------------------------------


def test_sandbox_rejects_nonexistent_root(tmp_path: Path):
    with pytest.raises(ValueError):
        FsSandbox(root=tmp_path / "missing")


def test_sandbox_blocks_parent_escape(tmp_path: Path):
    sb = FsSandbox(root=tmp_path)
    with pytest.raises(PathSecurityError):
        sb.resolve_for_read("../etc")


def test_sandbox_blocks_absolute_escape(tmp_path: Path):
    sb = FsSandbox(root=tmp_path)
    # On Windows, /etc may not be a valid absolute path; on POSIX it is.
    with pytest.raises(PathSecurityError):
        sb.resolve_for_read("/etc/passwd")


def test_sandbox_write_requires_existing_parent(tmp_path: Path):
    sb = FsSandbox(root=tmp_path)
    with pytest.raises(PathSecurityError):
        # .. tries to write to the parent of root, which has no parent
        sb.resolve_for_write("../../evil.txt")


def test_factory_returns_valid_config(tmp_path: Path):
    f = McpFilesystemFactory(root=tmp_path)
    cfg = f.config()
    assert cfg["command"] == sys.executable
    assert "-m" in cfg["args"]
    assert "kairos.mcp_filesystem_server" in cfg["args"]
    assert str(tmp_path.resolve()) in cfg["args"]
    assert cfg["enabled"] is True


def test_tool_list_directory_root(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("ok", encoding="utf-8")
    out = json.loads(tool_list_directory(FsSandbox(root=tmp_path), {"path": ""}))
    names = sorted(e["name"] for e in out["entries"])
    assert names == ["a.txt", "sub"]
    assert out["count"] == 2


def test_tool_list_directory_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        tool_list_directory(FsSandbox(root=tmp_path), {"path": "nope"})


def test_tool_read_file_with_range(tmp_path: Path):
    p = tmp_path / "lines.txt"
    p.write_text("\n".join(f"L{i:02d}" for i in range(50)), encoding="utf-8")
    out = json.loads(
        tool_read_file(FsSandbox(root=tmp_path), {"path": "lines.txt", "start": 10, "limit": 5})
    )
    assert out["total_lines"] == 50
    assert out["start"] == 10
    assert out["limit"] == 5
    lines = out["content"].splitlines()
    assert lines == ["L10", "L11", "L12", "L13", "L14"]


def test_tool_read_file_oversize_rejected(tmp_path: Path):
    big = tmp_path / "big.txt"
    big.write_text("x" * (_MAX_READ_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError):
        tool_read_file(FsSandbox(root=tmp_path), {"path": "big.txt"})


def test_tool_write_file_creates_and_overwrites(tmp_path: Path):
    sb = FsSandbox(root=tmp_path)
    out = json.loads(tool_write_file(sb, {"path": "new/hello.txt", "content": "hello"}))
    assert out["ok"] is True
    assert (tmp_path / "new" / "hello.txt").read_text(encoding="utf-8") == "hello"
    # overwrite
    tool_write_file(sb, {"path": "new/hello.txt", "content": "world"})
    assert (tmp_path / "new" / "hello.txt").read_text(encoding="utf-8") == "world"


def test_tool_search_files_finds_matches(tmp_path: Path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("def baz():\n", encoding="utf-8")
    out = json.loads(
        tool_search_files(FsSandbox(root=tmp_path), {"pattern": r"^def\s+\w+", "glob": "*.py"})
    )
    paths = sorted(m["path"] for m in out["matches"])
    assert paths == ["a.py", "b.py"]


def test_tool_search_files_truncates_at_max(tmp_path: Path):
    # write 250 lines that all match
    (tmp_path / "x.txt").write_text("match\n" * 300, encoding="utf-8")
    out = json.loads(
        tool_search_files(FsSandbox(root=tmp_path), {"pattern": "match"})
    )
    assert out["truncated"] is True
    assert out["count"] == 200  # _MAX_SEARCH_MATCHES


def test_tool_stat_reports_metadata(tmp_path: Path):
    (tmp_path / "f").write_text("abc", encoding="utf-8")
    out = json.loads(tool_stat(FsSandbox(root=tmp_path), {"path": "f"}))
    assert out["type"] == "file"
    assert out["size"] == 3


def test_all_tools_have_required_keys():
    for name, spec in _TOOLS.items():
        assert "description" in spec and spec["description"], name
        assert "input_schema" in spec, name
        assert "fn" in spec and callable(spec["fn"]), name
        assert spec["input_schema"].get("type") == "object", name


def test_build_mcp_server_exposes_all_tools():
    server, names = build_mcp_server(Path(__file__).parent.parent)
    assert isinstance(server, object)
    assert set(names) == set(_TOOLS.keys())


# ---------------------------------------------------------------------------
# End-to-end: real subprocess + real StdioMcpClient
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path) -> StdioMcpClient:
    factory = McpFilesystemFactory(root=tmp_path)
    cfg_dict = factory.config()
    cfg = McpServerConfig(
        name="kairos-fs",
        command=cfg_dict["command"],
        args=cfg_dict["args"],
        env=cfg_dict["env"],
        enabled=True,
    )
    return StdioMcpClient(cfg, request_timeout_s=15.0)


@pytest.mark.asyncio
async def test_subprocess_list_and_read_roundtrip(tmp_path: Path):
    """Spawn the real server, list, read, write, search."""
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "code.py").write_text("print('ok')\n", encoding="utf-8")

    def first_text(res: dict) -> str:
        return res["content"][0]["text"]

    client = _make_client(tmp_path)
    try:
        info = await client.start()
        assert "serverInfo" in info or "capabilities" in info or "protocolVersion" in info

        tools = await client.list_tools()
        names = sorted(t["name"] for t in tools)
        assert "list_directory" in names
        assert "read_file" in names
        assert "write_file" in names
        assert "search_files" in names
        assert "stat" in names

        # list root
        listing = await client.call_tool("list_directory", {"path": ""})
        assert listing["isError"] is False
        payload = json.loads(first_text(listing))
        assert payload["count"] == 2
        assert sorted(e["name"] for e in payload["entries"]) == ["hello.txt", "sub"]

        # read file
        reading = await client.call_tool("read_file", {"path": "hello.txt"})
        assert reading["isError"] is False
        read_payload = json.loads(first_text(reading))
        assert "hello world" in read_payload["content"]

        # write file
        writing = await client.call_tool(
            "write_file", {"path": "new.txt", "content": "from mcp\n"}
        )
        assert writing["isError"] is False
        assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "from mcp\n"

        # search files
        searching = await client.call_tool(
            "search_files", {"pattern": r"print", "glob": "*.py"}
        )
        assert searching["isError"] is False
        s = json.loads(first_text(searching))
        assert s["count"] >= 1
        assert "code.py" in s["matches"][0]["path"]

        # stat
        stat_res = await client.call_tool("stat", {"path": "hello.txt"})
        assert stat_res["isError"] is False
        st = json.loads(first_text(stat_res))
        assert st["type"] == "file"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_subprocess_rejects_escape_attempt(tmp_path: Path):
    """Subprocess must refuse path traversal even if the client tries."""
    client = _make_client(tmp_path)
    try:
        await client.start()
        bad = await client.call_tool("read_file", {"path": "../escape.txt"})
        assert bad["isError"] is True
        text = bad["content"][0]["text"].lower()
        assert "security" in text or "escapes" in text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_subprocess_unknown_tool_returns_error(tmp_path: Path):
    client = _make_client(tmp_path)
    try:
        await client.start()
        bad = await client.call_tool("no_such_tool", {})
        assert bad["isError"] is True
    finally:
        await client.close()
