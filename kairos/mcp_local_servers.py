"""Bundled MCP servers that need no ``npx``, no ``uvx`` and no download.

The registry in ``kairos/extensions/mcps.json`` lists twenty useful servers, but
almost all of them are ``npx -y ...`` / ``uvx ...``: enabling one means fetching
a package from npm or PyPI at first use, which is slow at best and impossible on
a locked-down machine. Exactly one server shipped locally
(:mod:`kairos.mcp_filesystem_server`), and the registry never pointed at it.

This module is the offline half: four servers that speak MCP over stdio using
the same SDK, run by *this* interpreter, with nothing to install.

    python -m kairos.mcp_local_servers --server git    --root /path/to/repo
    python -m kairos.mcp_local_servers --server sqlite --root /path/to.sqlite
    python -m kairos.mcp_local_servers --server time
    python -m kairos.mcp_local_servers --server fetch

``bundled_mcp_command`` returns the argv that will work in the current install
(a source checkout runs ``-m``; a frozen build cannot, so it asks the executable
for the same server by flag). Callers should prefer :func:`resolve_bundled`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Everything this module can serve, plus the module that already served one.
BUNDLED_SERVERS: Tuple[str, ...] = ("filesystem", "git", "sqlite", "time", "fetch")

_FILESYSTEM_MODULE = "kairos.mcp_filesystem_server"
_LOCAL_MODULE = "kairos.mcp_local_servers"

MAX_OUTPUT_BYTES = 64 * 1024
MAX_SQLITE_ROWS = 200
MAX_FETCH_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# how to launch a bundled server
# ---------------------------------------------------------------------------

def bundled_mcp_command(server: str,
                        root: Optional[Path] = None) -> Dict[str, Any]:
    """argv that starts ``server`` under the interpreter running *this* code.

    A source install can use ``-m``; a frozen build cannot (the executable is
    the application, not Python), so it passes a flag instead. Either way the
    command is local: no npm, no pip, no network.
    """
    if server not in BUNDLED_SERVERS:
        raise ValueError(f"unknown bundled server: {server!r}")
    module = _FILESYSTEM_MODULE if server == "filesystem" else _LOCAL_MODULE
    if getattr(sys, "frozen", False):
        args = ["--mcp-serve", server]
        if server == "filesystem" and root is not None:
            args += ["--root", str(Path(root).resolve())]
    elif server == "filesystem":
        args = ["-m", module]
        if root is not None:
            args.append(str(Path(root).resolve()))
    else:
        args = ["-m", module, "--server", server]
        if root is not None:
            args += ["--root", str(Path(root).resolve())]
    return {"command": sys.executable, "args": args, "transport": "stdio"}


def resolve_bundled(server: str,
                    root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Like :func:`bundled_mcp_command`, but ``None`` for unknown names."""
    try:
        return bundled_mcp_command(server, root)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# tool plumbing (mirrors kairos.mcp_filesystem_server's shape)
# ---------------------------------------------------------------------------

ToolSpec = Dict[str, Any]


def _tool(name: str, description: str, properties: Dict[str, Any],
          required: List[str], handler: Callable[..., str]) -> ToolSpec:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties,
                        "required": required},
        "handler": handler,
    }


def _clip(text: str, limit: int = MAX_OUTPUT_BYTES) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated at {limit} bytes]"


def _run_git(root: Path, args: List[str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and not out.strip():
        out = f"git exited {proc.returncode}"
    return _clip(out.strip() or "(no output)")


# ---------------------------------------------------------------------------
# server: git (offline)
# ---------------------------------------------------------------------------

def _git_tools(root: Path) -> Dict[str, ToolSpec]:
    def status() -> str:
        return _run_git(root, ["status", "--short", "--branch"])

    def log(limit: int = 20) -> str:
        return _run_git(root, ["log", f"-{int(limit)}", "--oneline",
                               "--decorate", "--no-color"])

    def diff(staged: bool = False, path: str = "") -> str:
        args = ["diff", "--no-color"]
        if staged:
            args.append("--cached")
        if path:
            args += ["--", path]
        return _run_git(root, args)

    def show(rev: str = "HEAD") -> str:
        return _run_git(root, ["show", "--stat", "--no-color", rev])

    def branches() -> str:
        return _run_git(root, ["branch", "--all", "--no-color"])

    return {
        "git_status": _tool("git_status", "Working tree status, short form.",
                            {}, [], status),
        "git_log": _tool("git_log", "Recent commits, one line each.",
                         {"limit": {"type": "integer", "default": 20}},
                         [], log),
        "git_diff": _tool("git_diff", "Diff of the working tree (or staged).",
                          {"staged": {"type": "boolean", "default": False},
                           "path": {"type": "string", "default": ""}},
                          [], diff),
        "git_show": _tool("git_show", "A commit with its file stats.",
                          {"rev": {"type": "string", "default": "HEAD"}},
                          [], show),
        "git_branches": _tool("git_branches", "All branches.",
                              {}, [], branches),
    }


# ---------------------------------------------------------------------------
# server: sqlite (offline, read-only)
# ---------------------------------------------------------------------------

def _sqlite_tools(root: Path) -> Dict[str, ToolSpec]:
    db_path = root if root.is_file() else root / "kairos.db"

    def _connect() -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def list_tables() -> str:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "ORDER BY name").fetchall()
        return "\n".join(r[0] for r in rows) or "(no tables)"

    def schema(table: str = "") -> str:
        with _connect() as conn:
            if table:
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = ?",
                    (table,)).fetchone()
                return (row[0] if row else f"(no such table: {table})")
            rows = conn.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "ORDER BY name").fetchall()
        return _clip("\n\n".join(r[0] for r in rows) or "(empty schema)")

    def query(sql: str, limit: int = 50) -> str:
        stmt = (sql or "").strip().rstrip(";")
        # Read-only by construction: the connection is opened read-only, and we
        # additionally refuse anything that is not a single SELECT/WITH.
        if not re.match(r"^(select|with)\b", stmt, re.I):
            return "refused: only SELECT/WITH statements are allowed"
        if ";" in stmt:
            return "refused: one statement at a time"
        cap = max(1, min(int(limit or 50), MAX_SQLITE_ROWS))
        with _connect() as conn:
            rows = conn.execute(stmt).fetchmany(cap)
        if not rows:
            return "(no rows)"
        cols = list(rows[0].keys())
        lines = ["\t".join(cols)]
        lines += ["\t".join("" if v is None else str(v) for v in tuple(r))
                  for r in rows]
        return _clip("\n".join(lines))

    return {
        "sqlite_tables": _tool("sqlite_tables",
                               "List tables and views in the database.",
                               {}, [], list_tables),
        "sqlite_schema": _tool("sqlite_schema",
                               "CREATE statements; pass a table for one.",
                               {"table": {"type": "string", "default": ""}},
                               [], schema),
        "sqlite_query": _tool("sqlite_query",
                              "Run a read-only SELECT/WITH query.",
                              {"sql": {"type": "string"},
                               "limit": {"type": "integer", "default": 50}},
                              ["sql"], query),
    }


# ---------------------------------------------------------------------------
# server: time (offline)
# ---------------------------------------------------------------------------

def _time_tools(_root: Path) -> Dict[str, ToolSpec]:
    def now(tz_offset_hours: float = 0.0) -> str:
        from datetime import timedelta
        tz = timezone(timedelta(hours=float(tz_offset_hours or 0)))
        moment = datetime.now(tz)
        return json.dumps({
            "iso": moment.isoformat(),
            "utc": moment.astimezone(timezone.utc).isoformat(),
            "unix": int(moment.timestamp()),
            "weekday": moment.strftime("%A"),
            "offsetHours": float(tz_offset_hours or 0),
        }, ensure_ascii=False)

    def parse(text: str) -> str:
        raw = (text or "").strip()
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception as exc:
            return f"could not parse {raw!r}: {exc}"
        return json.dumps({
            "iso": moment.isoformat(),
            "unix": int(moment.timestamp()),
        }, ensure_ascii=False)

    return {
        "time_now": _tool("time_now",
                          "Current time as ISO-8601, UTC, unix and weekday.",
                          {"tz_offset_hours": {"type": "number", "default": 0}},
                          [], now),
        "time_parse": _tool("time_parse",
                            "Parse an ISO-8601 timestamp.",
                            {"text": {"type": "string"}}, ["text"], parse),
    }


# ---------------------------------------------------------------------------
# server: fetch (needs the network, by definition)
# ---------------------------------------------------------------------------

def _fetch_tools(_root: Path) -> Dict[str, ToolSpec]:
    def http_get(url: str, max_bytes: int = MAX_FETCH_BYTES) -> str:
        target = (url or "").strip()
        if not re.match(r"^https?://", target, re.I):
            return "refused: only http(s) URLs"
        req = urllib.request.Request(
            target, headers={"User-Agent": "kairos-mcp-fetch/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            body = resp.read(max(1, min(int(max_bytes or MAX_FETCH_BYTES),
                                        MAX_FETCH_BYTES)))
            charset = resp.headers.get_content_charset() or "utf-8"
        return _clip(body.decode(charset, errors="replace"))

    return {
        "http_get": _tool("http_get",
                          "GET an http(s) URL and return the body as text.",
                          {"url": {"type": "string"},
                           "max_bytes": {"type": "integer",
                                         "default": MAX_FETCH_BYTES}},
                          ["url"], http_get),
    }


_BUILDERS: Dict[str, Callable[[Path], Dict[str, ToolSpec]]] = {
    "git": _git_tools,
    "sqlite": _sqlite_tools,
    "time": _time_tools,
    "fetch": _fetch_tools,
}


# ---------------------------------------------------------------------------
# MCP server construction
# ---------------------------------------------------------------------------

def tools_for(server: str, root: Optional[Path] = None) -> Dict[str, ToolSpec]:
    """The tool table for ``server`` (no SDK needed)."""
    builder = _BUILDERS.get(server)
    if builder is None:
        raise ValueError(f"unknown bundled server: {server!r}")
    return builder(Path(root or "."))


def build_mcp_server(server: str, root: Optional[Path] = None):
    """Construct an ``mcp.server.Server`` for ``server``."""
    from mcp.server import Server
    from mcp.types import (CallToolResult, ListToolsResult, TextContent, Tool)

    table = tools_for(server, root)

    async def _on_list_tools(_ctx, _params) -> ListToolsResult:
        return ListToolsResult(tools=[
            Tool(name=spec["name"], description=spec["description"],
                 inputSchema=spec["inputSchema"])
            for spec in table.values()
        ])

    async def _on_call_tool(_ctx, params) -> CallToolResult:
        spec = table.get(getattr(params, "name", ""))
        if spec is None:
            return CallToolResult(
                content=[TextContent(type="text",
                                     text=f"unknown tool: {params.name}")],
                isError=True)
        args = dict(getattr(params, "arguments", None) or {})
        try:
            text = spec["handler"](**args)
        except TypeError as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=f"bad request: {exc}")],
                isError=True)
        except Exception as exc:
            return CallToolResult(
                content=[TextContent(type="text",
                                     text=f"internal error: {type(exc).__name__}: {exc}")],
                isError=True)
        return CallToolResult(
            content=[TextContent(type="text", text=str(text))])

    server_obj = Server(
        f"kairos-{server}",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )
    return server_obj, list(table.keys())


async def _serve_async(server: str, root: Optional[Path]) -> None:
    from mcp.server.stdio import stdio_server
    obj, _names = build_mcp_server(server, root)
    async with stdio_server() as (read, write):
        await obj.run(read, write, obj.create_initialization_options())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kairos.mcp_local_servers",
        description="Bundled, offline MCP servers for Kairos Code.")
    parser.add_argument("--server", required=True,
                        choices=[s for s in BUNDLED_SERVERS
                                 if s != "filesystem"],
                        help="which bundled server to run")
    parser.add_argument("--root", default=".",
                        help="repository / database / working directory")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING))
    asyncio.run(_serve_async(args.server, Path(args.root)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
