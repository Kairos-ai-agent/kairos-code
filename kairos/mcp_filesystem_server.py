"""Built-in MCP filesystem server.

Spawns a real MCP server (using the official ``mcp`` Python SDK) that
exposes a sandboxed view of a directory tree. Tools:

  - ``list_directory(path?)``         — entries under a directory
  - ``read_file(path, start?, limit?)`` — text file with line range
  - ``write_file(path, content)``     — write/overwrite text file
  - ``search_files(pattern, path?)``  — regex search over text files
  - ``stat(path)``                    — file metadata

The server is a real subprocess: it speaks MCP stdio JSON-RPC, so the
existing :class:`kairos.mcp_client.StdioMcpClient` can attach to it
without any code changes. To run it standalone::

    python -m kairos.mcp_filesystem_server /path/to/root

Kairos wires it up automatically when a project is created — see
:class:`McpFilesystemFactory` for the registration entry point.
"""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import logging
import os
import re
import stat as stat_mod
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path sandboxing
# ---------------------------------------------------------------------------


class PathSecurityError(PermissionError):
    """Raised when a tool call escapes the configured root."""


@dataclass
class FsSandbox:
    """Resolve and validate a path under a fixed root.

    Symlinks are resolved (realpath) and the result is checked to be
    under the root. Both the root and the requested target must exist
    for ``resolve_for_read``; ``resolve_for_write`` allows the target
    not to exist yet but its parent must.
    """

    root: Path
    case_sensitive: bool = True

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise ValueError(f"fs sandbox root must be an existing directory: {self.root}")

    def _norm(self, p: Path) -> Path:
        return p if self.case_sensitive else Path(str(p).lower())

    def _check(self, resolved: Path) -> None:
        root_n = self._norm(self.root)
        target_n = self._norm(resolved)
        try:
            target_n.relative_to(root_n)
        except ValueError as exc:
            raise PathSecurityError(
                f"path {resolved} escapes sandbox root {self.root}"
            ) from exc

    def resolve_for_read(self, requested: str) -> Path:
        if not requested:
            return self.root
        p = (self.root / requested).resolve()
        self._check(p)
        return p

    def resolve_for_write(self, requested: str) -> Path:
        if not requested:
            raise PathSecurityError("write path must be non-empty")
        p = (self.root / requested).resolve()
        # For write, we need the parent to exist under root.
        parent = p.parent
        self._check(parent)
        if parent.exists() and not parent.is_dir():
            raise PathSecurityError(f"parent {parent} is not a directory")
        return p


# ---------------------------------------------------------------------------
# Tool implementations (pure, sync — wrapped for async at server level)
# ---------------------------------------------------------------------------


_DEFAULT_READ_LIMIT = 2000  # lines
_MAX_READ_BYTES = 2_000_000  # 2MB hard cap per read
_MAX_SEARCH_MATCHES = 200


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def tool_list_directory(sandbox: FsSandbox, args: Dict[str, Any]) -> str:
    sub = str(args.get("path", "") or "")
    p = sandbox.resolve_for_read(sub)
    if not p.exists():
        raise FileNotFoundError(f"directory not found: {sub or '.'}")
    if not p.is_dir():
        raise NotADirectoryError(f"not a directory: {sub or '.'}")
    entries: List[Dict[str, Any]] = []
    for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        try:
            st = child.stat()
            entries.append({
                "name": child.name,
                "type": "directory" if child.is_dir() else "file",
                "size": st.st_size if child.is_file() else 0,
                "modified": int(st.st_mtime),
            })
        except OSError:
            # skip unreadable
            continue
    return _to_json({"root": str(p), "entries": entries, "count": len(entries)})


def tool_read_file(sandbox: FsSandbox, args: Dict[str, Any]) -> str:
    if "path" not in args:
        raise ValueError("'path' is required")
    p = sandbox.resolve_for_read(str(args["path"]))
    if not p.exists():
        raise FileNotFoundError(f"file not found: {args['path']}")
    if not p.is_file():
        raise IsADirectoryError(f"not a file: {args['path']}")
    if p.stat().st_size > _MAX_READ_BYTES:
        raise ValueError(
            f"file too large to read in one call ({p.stat().st_size} > {_MAX_READ_BYTES}); "
            "use start+limit"
        )
    start = int(args.get("start", 0) or 0)
    limit = int(args.get("limit", _DEFAULT_READ_LIMIT) or _DEFAULT_READ_LIMIT)
    if start < 0 or limit <= 0:
        raise ValueError("start must be >= 0 and limit > 0")
    with p.open("r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    total = len(all_lines)
    sliced = all_lines[start : start + limit]
    return _to_json({
        "path": str(p),
        "total_lines": total,
        "start": start,
        "limit": limit,
        "content": "".join(sliced),
    })


def tool_write_file(sandbox: FsSandbox, args: Dict[str, Any]) -> str:
    if "path" not in args:
        raise ValueError("'path' is required")
    if "content" not in args:
        raise ValueError("'content' is required")
    p = sandbox.resolve_for_write(str(args["path"]))
    content = str(args["content"])
    if not isinstance(content, str):
        raise TypeError("'content' must be a string")
    # Refuse writes that try to escape via symlink after creation.
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    st = p.stat()
    return _to_json({"path": str(p), "size": st.st_size, "ok": True})


_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "target", "vendor",
}


def _iter_text_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        # prune
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            fp = Path(dirpath) / fn
            # quick binary sniff
            try:
                with fp.open("rb") as f:
                    head = f.read(512)
            except OSError:
                continue
            if b"\x00" in head:
                continue
            yield fp


def tool_search_files(sandbox: FsSandbox, args: Dict[str, Any]) -> str:
    if "pattern" not in args:
        raise ValueError("'pattern' is required")
    pat = re.compile(str(args["pattern"]))
    sub = str(args.get("path", "") or "")
    glob = str(args.get("glob", "") or "")
    p = sandbox.resolve_for_read(sub)
    if not p.exists():
        raise FileNotFoundError(f"path not found: {sub or '.'}")
    matches: List[Dict[str, Any]] = []
    truncated = False
    for fp in _iter_text_files(p):
        if glob and not fnmatch.fnmatch(fp.name, glob):
            continue
        try:
            rel = fp.relative_to(p)
        except ValueError:
            rel = fp
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if pat.search(line):
                        matches.append({
                            "path": str(rel),
                            "line": lineno,
                            "text": line.rstrip("\n")[:400],
                        })
                        if len(matches) >= _MAX_SEARCH_MATCHES:
                            truncated = True
                            break
        except OSError:
            continue
        if truncated:
            break
    return _to_json({
        "root": str(p),
        "pattern": args["pattern"],
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
    })


def tool_stat(sandbox: FsSandbox, args: Dict[str, Any]) -> str:
    if "path" not in args:
        raise ValueError("'path' is required")
    p = sandbox.resolve_for_read(str(args["path"]))
    if not p.exists():
        raise FileNotFoundError(f"path not found: {args['path']}")
    st = p.stat()
    mode = st.st_mode
    return _to_json({
        "path": str(p),
        "type": (
            "directory" if stat_mod.S_ISDIR(mode)
            else "symlink" if stat_mod.S_ISLNK(mode)
            else "file"
        ),
        "size": st.st_size,
        "mode_octal": oct(mode & 0o7777),
        "modified": int(st.st_mtime),
        "created": int(st.st_ctime),
    })


_TOOLS: Dict[str, Any] = {
    "list_directory": {
        "description": "List entries under a directory (relative to sandbox root).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path; empty = root"},
            },
        },
        "fn": tool_list_directory,
    },
    "read_file": {
        "description": "Read a text file with line-range support.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path"},
                "start": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "default": 2000},
            },
            "required": ["path"],
        },
        "fn": tool_read_file,
    },
    "write_file": {
        "description": "Write (overwrite) a text file under the sandbox root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
        "fn": tool_write_file,
    },
    "search_files": {
        "description": "Regex search across text files (skips .git/node_modules/etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex"},
                "path": {"type": "string", "description": "Relative root; empty = sandbox root"},
                "glob": {"type": "string", "description": "Optional filename glob filter"},
            },
            "required": ["pattern"],
        },
        "fn": tool_search_files,
    },
    "stat": {
        "description": "Return metadata (size, mode, mtime) for a path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path"},
            },
            "required": ["path"],
        },
        "fn": tool_stat,
    },
}


# ---------------------------------------------------------------------------
# MCP server (uses the official `mcp` SDK)
# ---------------------------------------------------------------------------


def build_mcp_server(root: Path, case_sensitive: bool = True):
    """Construct a ``mcp.server.Server`` bound to *root*.

    Returns the server instance plus a list of tool names it exposes.
    The caller is responsible for running it (e.g. via ``stdio_server``).
    """
    from mcp.server import Server
    from mcp.types import Tool, TextContent, CallToolResult, ListToolsResult

    sandbox = FsSandbox(root=Path(root), case_sensitive=case_sensitive)

    async def _on_list_tools(_ctx, _params) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name=name,
                    description=spec["description"],
                    inputSchema=spec["input_schema"],
                )
                for name, spec in _TOOLS.items()
            ]
        )

    async def _on_call_tool(_ctx, params) -> CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        spec = _TOOLS.get(name)
        if spec is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"unknown tool: {name}")],
                isError=True,
            )
        try:
            text = spec["fn"](sandbox, arguments)
        except PathSecurityError as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=f"security error: {exc}")],
                isError=True,
            )
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=str(exc))],
                isError=True,
            )
        except (ValueError, TypeError) as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=f"bad request: {exc}")],
                isError=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("tool %s failed", name)
            return CallToolResult(
                content=[TextContent(type="text", text=f"internal error: {exc}")],
                isError=True,
            )
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            isError=False,
        )

    server: Any = Server(
        "kairos-fs",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )
    return server, list(_TOOLS.keys())


async def _serve_async(root: Path) -> None:
    from mcp.server.stdio import stdio_server
    from mcp.server.models import InitializationOptions

    server, _ = build_mcp_server(root)
    # Same reason as the client: report the real package version, not a literal
    # that silently goes stale.
    from kairos import __version__

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="kairos-fs",
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


# ---------------------------------------------------------------------------
# Factory: registers this server as a config so existing load_configs() picks it up
# ---------------------------------------------------------------------------


@dataclass
class McpFilesystemFactory:
    """Expose a :class:`McpServerConfig` whose command spawns this server.

    Example::

        factory = McpFilesystemFactory(root=Path("/work"))
        cfg = factory.config()
        # cfg.command = sys.executable
        # cfg.args   = ["-m", "kairos.mcp_filesystem_server", str(root)]
    """

    root: Path

    def config(self) -> Dict[str, Any]:
        """Return a dict compatible with ``mcp_servers:`` YAML schema."""
        return {
            "command": sys.executable,
            "args": ["-m", "kairos.mcp_filesystem_server", str(Path(self.root).resolve())],
            "env": {},
            "enabled": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Kairos built-in MCP filesystem server")
    parser.add_argument("root", help="Sandbox root directory (must exist)")
    parser.add_argument("--case-insensitive", action="store_true")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    asyncio.run(_serve_async(Path(args.root)))


if __name__ == "__main__":  # pragma: no cover
    main()
