"""MCP (Model Context Protocol) client.

Reference: https://modelcontextprotocol.io — 2025-11-25 spec
(2026-07-28 stateless revision is wire-compatible with this client).

MCP is the de-facto standard for connecting AI agents to external
data sources (databases, GitHub, Linear, Figma, custom tools). Both
the cloud task and the agentic CLI ship first-class MCP clients; without MCP,
Kairos is locked out of the entire ecosystem.

This module implements the **client** side only. A server is a
subprocess that speaks JSON-RPC 2.0 over its stdin/stdout. The
client:

  1. Spawns the server with `command` + `args` + `env`.
  2. Sends `initialize` → waits for `InitializeResult`.
  3. Sends `notifications/initialized` (per spec).
  4. Sends `tools/list` → returns the server's tool catalog.
  5. Wraps each tool as a `BaseTool` so existing `KairosAgent`
     can call it via the standard tool-calling loop (no protocol
     changes needed in `KairosAgent`).
  6. For each `tools/call` invocation, sends the request and waits
     for the result (which can be a normal payload or a `CallToolResult`
     with `isError=true`).

Wire format: newline-delimited JSON. One JSON object per line on each
direction. Notifications (no id) are also one-line JSON. This is the
"stdio" transport from the spec.

Configuration is read from YAML:

    mcp_servers:
      github:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-github"]
        env:
          GITHUB_TOKEN: ${GITHUB_TOKEN}
      postgres:
        command: uvx
        args: ["mcp-server-postgres"]
        env:
          DATABASE_URL: postgres://...

Servers can be at:
  - <project.work_dir>/.kairos/mcp.yaml  (project scope)
  - ~/.kairos/mcp.yaml                   (user scope)
Both are loaded and merged; project wins on name collision.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


MCP_PROTOCOL_VERSION = "2025-11-25"
DEFAULT_REQUEST_TIMEOUT_S = 60.0


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


@dataclass
class McpServerConfig:
    """One MCP server definition, ready to spawn or connect to."""
    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    # R38.12: "stdio" (spawn `command`) or a remote transport — "http"
    # (Streamable HTTP) / "sse" — which needs `url` and optionally `headers`.
    transport: str = "stdio"
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    # Optional connection hints. Defaults below match the spec.
    protocol_version: str = MCP_PROTOCOL_VERSION

    def expanded_headers(self) -> Dict[str, str]:
        """``headers`` with ``${VAR}`` references filled in, like ``env``."""
        out: Dict[str, str] = {}
        for k, v in (self.headers or {}).items():
            out[k] = os.path.expandvars(v) if isinstance(v, str) and "$" in v else v
        return out

    def expanded_env(self) -> Dict[str, str]:
        """Return a copy of env with ${VAR} references substituted from
        the host environment. Missing references stay literal so the
        server can complain about its own configuration.

        We use ``os.path.expandvars`` (POSIX-style $VAR and ${VAR})
        rather than ``str.format`` because env values legitimately
        contain things like passwords and ``str.format`` would
        misinterpret any ``{...}`` they happen to contain.
        """
        out: Dict[str, str] = {}
        for k, v in self.env.items():
            if isinstance(v, str) and ("$" in v):
                out[k] = os.path.expandvars(v)
            else:
                out[k] = v
        return out


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml_config(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except yaml.YAMLError as exc:
        logger.warning("mcp: bad YAML in %s: %s; skipping", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _merged_servers(
    project_dir: Optional[Path] = None,
    user_dir: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """Raw server entries from both YAML files; project wins, deep-merged."""
    merged_servers: Dict[str, Dict[str, Any]] = {}

    if user_dir:
        user_cfg = _load_yaml_config(Path(user_dir) / "mcp.yaml")
        for name, server in (user_cfg.get("mcp_servers") or {}).items():
            if isinstance(server, dict):
                merged_servers[name] = dict(server)
                merged_servers[name].setdefault("name", name)
    if project_dir:
        proj_cfg = _load_yaml_config(Path(project_dir) / ".kairos" / "mcp.yaml")
        for name, server in (proj_cfg.get("mcp_servers") or {}).items():
            if isinstance(server, dict):
                existing = merged_servers.get(name, {})
                merged_servers[name] = _deep_merge(existing, {**server, "name": name})
    return merged_servers


def load_configs(
    project_dir: Optional[Path] = None,
    user_dir: Optional[Path] = None,
) -> Dict[str, McpServerConfig]:
    """Load MCP server configs from project + user YAML, project wins.

    Lookup paths:
      1. <project_dir>/.kairos/mcp.yaml
      2. <user_dir or ~/.kairos>/mcp.yaml

    An entry that cannot be built is skipped with a warning — see
    :func:`audit_configs` for the same selection *with* the reasons.
    """
    user_dir = Path(user_dir) if user_dir else Path.home() / ".kairos"
    out: Dict[str, McpServerConfig] = {}
    for name, raw in _merged_servers(project_dir, user_dir).items():
        config, problem = _build_config(name, raw)
        if problem:
            logger.warning("mcp: server %s: %s; skipping", name, problem)
            continue
        out[name] = config
    return out

def _build_config(
    name: str, raw: Dict[str, Any],
) -> Tuple[Optional[McpServerConfig], Optional[str]]:
    """One raw entry → a config, or the reason it was rejected.

    Split out of :func:`load_configs` so the capability view can explain a
    server the runtime skipped: a config that vanishes without a trace is how
    "I set it up and nothing happened" stays unexplained.
    """
    transport = str(raw.get("transport") or "stdio").strip().lower()
    url = str(raw.get("url") or "").strip()
    cmd = raw.get("command")

    if transport in ("http", "sse"):
        # A remote server: no subprocess and no PATH lookup — just an endpoint
        # (and, usually, an auth header).
        if not url:
            return None, f"transport {transport!r} needs a url"
        return McpServerConfig(
            name=name,
            transport=transport,
            url=url,
            headers=dict(raw.get("headers") or {}),
            enabled=bool(raw.get("enabled", True)),
        ), None

    if transport != "stdio":
        return None, f"unknown transport {transport!r}"

    if not cmd or not isinstance(cmd, str):
        return None, "no command"

    # Only PATH-resolved commands need shutil.which(). Absolute paths are taken
    # at face value — the launcher produces a clear OSError if they are wrong.
    if not os.path.isabs(cmd) and not shutil.which(cmd):
        return None, f"command {cmd!r} not found on PATH"

    return McpServerConfig(
        name=name,
        command=cmd,
        args=list(raw.get("args") or []),
        env=dict(raw.get("env") or {}),
        enabled=bool(raw.get("enabled", True)),
        transport="stdio",
    ), None


def audit_configs(
    project_dir: Optional[Path] = None,
    user_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Everything configured, including what ``load_configs`` drops and why.

    The same selection as :func:`load_configs`, plus a ``rejected`` map of
    server → reason. Read-only: nothing is started, nothing is written.
    """
    user_dir = Path(user_dir) if user_dir else Path.home() / ".kairos"
    servers: Dict[str, McpServerConfig] = {}
    rejected: Dict[str, str] = {}
    for name, raw in _merged_servers(project_dir, user_dir).items():
        config, problem = _build_config(name, raw)
        if problem:
            rejected[name] = problem
        else:
            servers[name] = config
    return {"servers": servers, "rejected": rejected}

# ---------------------------------------------------------------------------
# Stdio JSON-RPC transport
# ---------------------------------------------------------------------------


class McpError(RuntimeError):
    """Wraps a JSON-RPC error returned by the server."""


class StdioMcpClient:
    """One MCP client bound to a single server subprocess.

    Lifecycle:
        client = StdioMcpClient(config)
        await client.start()
        tools = await client.list_tools()
        result = await client.call_tool("foo", {"x": 1})
        await client.close()
    """

    def __init__(
        self,
        config: McpServerConfig,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ):
        self.config = config
        self._timeout = request_timeout_s
        self._process: Optional[asyncio.subprocess.Process] = None
        self._next_id = 1
        self._lock = asyncio.Lock()  # serialize writes to the subprocess
        # server → client notifications that arrive between requests.
        # We don't act on them today but we read them off the wire so
        # the read pipe doesn't stall.
        self._notification_log: List[Dict[str, Any]] = []
        self._reader_task: Optional[asyncio.Task] = None
        # The version this client announces to servers. Read from the package
        # instead of repeating the literal: it had drifted to "0.1.0" and would
        # have kept reporting that forever.
        from kairos import __version__  # local import: keeps the MCP stack out of package init

        self._client_info = {"name": "kairos", "version": __version__}
        self._server_info: Optional[Dict[str, Any]] = None

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> Dict[str, Any]:
        """Spawn the subprocess and complete the initialize handshake."""
        if self._process is not None:
            return self._server_info or {}
        env = {**os.environ, **self.config.expanded_env()}
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise McpError(
                f"MCP server {self.config.name!r}: command {self.config.command!r} not found"
            ) from exc
        except OSError as exc:
            raise McpError(
                f"MCP server {self.config.name!r}: failed to start: {exc}"
            ) from exc

        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"mcp-reader-{self.config.name}"
        )

        result = await self._request("initialize", {
            "protocolVersion": self.config.protocol_version,
            "capabilities": {"roots": {"listChanged": False}},
            "clientInfo": self._client_info,
        })
        self._server_info = result
        # Per spec, the client must send `notifications/initialized`
        # after a successful initialize response. It's a notification
        # (no id, no response expected).
        await self._notify("notifications/initialized", {})
        return result

    async def close(self) -> None:
        """Tear down the subprocess. Idempotent."""
        if self._process is None:
            return
        try:
            if self._process.stdin and not self._process.stdin.is_closing():
                self._process.stdin.close()
        except Exception:
            pass
        try:
            # Give the server a moment to flush, then SIGTERM.
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
        except ProcessLookupError:
            pass
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        self._process = None
        self._reader_task = None

    async def __aenter__(self) -> "StdioMcpClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- JSON-RPC primitives ---------------------------------------------

    async def _read_loop(self) -> None:
        """Background task: read newline-delimited JSON from server stdout.

        Responses are matched to outstanding requests by `id`. Anything
        without an id is treated as a notification and logged.
        """
        assert self._process and self._process.stdout
        while True:
            line = await self._process.stdout.readline()
            if not line:
                return
            try:
                msg = json.loads(line.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(msg, dict):
                continue
            # Match by id to outstanding request futures.
            if "id" in msg and isinstance(msg["id"], int):
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg)
            else:
                self._notification_log.append(msg)

    async def _request(self, method: str, params: Dict[str, Any]) -> Any:
        """Send a JSON-RPC request and await the response."""
        if not self._process or not self._process.stdin or self._process.stdin.is_closing():
            raise McpError("client not started")
        async with self._lock:
            req_id = self._next_id
            self._next_id += 1
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[req_id] = fut
            payload = json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }, ensure_ascii=False) + "\n"
            try:
                self._process.stdin.write(payload.encode("utf-8"))
                await self._process.stdin.drain()
            except (ConnectionResetError, BrokenPipeError) as exc:
                self._pending.pop(req_id, None)
                raise McpError(
                    f"server {self.config.name!r} closed connection mid-request"
                ) from exc
        try:
            response = await asyncio.wait_for(fut, timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise McpError(
                f"MCP request {method!r} timed out after {self._timeout:.0f}s"
            ) from exc
        if "error" in response:
            err = response["error"]
            raise McpError(
                f"server {self.config.name!r} returned error for {method!r}: "
                f"{err.get('message')!r} (code={err.get('code')})"
            )
        return response.get("result")

    async def _notify(self, method: str, params: Dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, no response)."""
        if not self._process or not self._process.stdin or self._process.stdin.is_closing():
            return
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }, ensure_ascii=False) + "\n"
        try:
            self._process.stdin.write(payload.encode("utf-8"))
            await self._process.stdin.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            logger.debug("mcp: notify %s failed: %s", method, exc)

    # pending id → future
    _pending: Dict[int, asyncio.Future] = {}

    # -- MCP methods ------------------------------------------------------

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Return the server's tool catalog (raw JSON Schema)."""
        result = await self._request("tools/list", {})
        return list(result.get("tools") or [])

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Invoke a tool on the server and return its `CallToolResult`."""
        return await self._request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

    @property
    def server_info(self) -> Optional[Dict[str, Any]]:
        return self._server_info


# ---------------------------------------------------------------------------
# Remote transports: Streamable HTTP and SSE
# ---------------------------------------------------------------------------


def _init_result_to_dict(info: Any) -> Dict[str, Any]:
    """The SDK's initialize result as a plain dict, with the stdio spelling."""
    if hasattr(info, "model_dump"):
        data = info.model_dump(by_alias=True, exclude_none=True)
    elif isinstance(info, dict):
        data = info
    else:
        data = {}
    server = data.get("serverInfo") or data.get("server_info") or {}
    return {
        "serverInfo": server,
        "protocolVersion": data.get("protocolVersion", MCP_PROTOCOL_VERSION),
        "capabilities": data.get("capabilities", {}),
    }


def _read_write(streams: Any) -> Tuple[Any, Any]:
    """Pull the two MCP streams out of whatever the SDK handed back.

    ``streamable_http_client`` yields a ``TransportStreams`` object in the SDK
    version this was built against and a tuple in others. Both are accepted here
    rather than pinned to one — the live test decides which is which.
    """
    if hasattr(streams, "read") and hasattr(streams, "write"):
        return streams.read, streams.write
    if isinstance(streams, (tuple, list)) and len(streams) >= 2:
        return streams[0], streams[1]
    raise McpError(
        f"unexpected MCP transport streams: {type(streams).__name__}")


class HttpMcpClient:
    """An MCP client for a *remote* server, over the official SDK transports.

    Same surface as :class:`StdioMcpClient` — ``start`` / ``list_tools`` /
    ``call_tool`` / ``server_info`` / ``close`` / async context manager — so
    ``McpToolAdapter`` and ``McpRegistry`` cannot tell the two apart. Nothing is
    spawned: the server is an endpoint and credentials travel in ``headers``
    (``${VAR}`` references expand from the host environment, like ``env``).

    ``transport: http`` is Streamable HTTP; ``transport: sse`` speaks the older
    SSE transport that some deployments still require.
    """

    def __init__(self, config: McpServerConfig,
                 request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S):
        self.config = config
        self._timeout = request_timeout_s
        self._stack: Optional[Any] = None
        self._session: Optional[Any] = None
        self._server_info: Optional[Dict[str, Any]] = None

    async def start(self) -> Dict[str, Any]:
        """Open the transport and complete the initialize handshake."""
        if self._session is not None:
            return self._server_info or {}
        if not self.config.url:
            raise McpError(
                f"MCP server {self.config.name!r}: transport "
                f"{self.config.transport!r} needs a 'url'")

        from contextlib import AsyncExitStack

        from mcp import ClientSession

        headers = self.config.expanded_headers() or None
        stack = AsyncExitStack()
        try:
            if self.config.transport == "sse":
                from mcp.client.sse import sse_client
                read, write = await stack.enter_async_context(
                    sse_client(self.config.url, headers=headers))
            else:
                from mcp.client.streamable_http import (create_mcp_http_client,
                                                        streamable_http_client)
                # Headers ride on the http client in this SDK, not on the
                # transport call — the two were merged in later versions.
                http_client = create_mcp_http_client(headers=headers) if headers else None
                streams = await stack.enter_async_context(
                    streamable_http_client(self.config.url, http_client=http_client))
                read, write = _read_write(streams)
            session = await stack.enter_async_context(ClientSession(read, write))
            info = await session.initialize()
            self._stack = stack
            self._session = session
            self._server_info = _init_result_to_dict(info)
        except Exception as exc:
            await stack.aclose()
            raise McpError(
                f"MCP server {self.config.name!r}: {self.config.transport} "
                f"connection failed: {exc}") from exc
        return self._server_info or {}

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as exc:  # a dead socket must not mask shutdown
                logger.debug("mcp: %s close failed: %s", self.config.name, exc)

    async def __aenter__(self) -> "HttpMcpClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def list_tools(self) -> List[Dict[str, Any]]:
        if self._session is None:
            raise McpError(f"MCP server {self.config.name!r}: not connected")
        result = await self._session.list_tools()
        out: List[Dict[str, Any]] = []
        for tool in (getattr(result, "tools", None) or []):
            if hasattr(tool, "model_dump"):
                # by_alias keeps the spec's camelCase (inputSchema), which is
                # what McpToolAdapter reads.
                out.append(tool.model_dump(by_alias=True, exclude_none=True))
            elif isinstance(tool, dict):
                out.append(tool)
        return out

    async def call_tool(self, name: str,
                        arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._session is None:
            raise McpError(f"MCP server {self.config.name!r}: not connected")
        result = await self._session.call_tool(name, arguments or {})
        if hasattr(result, "model_dump"):
            return result.model_dump(by_alias=True, exclude_none=True)
        return result if isinstance(result, dict) else {"content": []}

    def server_info(self) -> Optional[Dict[str, Any]]:
        return self._server_info


def client_for(config: McpServerConfig):
    """Build the client a config's transport calls for."""
    if config.transport in ("http", "sse"):
        return HttpMcpClient(config)
    return StdioMcpClient(config)


# ---------------------------------------------------------------------------
# Kairos adapter: wrap an MCP tool as a BaseTool
# ---------------------------------------------------------------------------


class McpToolAdapter(BaseTool):
    """Adapts a single MCP tool to the Kairos BaseTool interface.

    The tool's JSON-Schema from `tools/list` becomes the BaseTool's
    `description` and `parameters`. `execute()` calls `tools/call`
    on the parent client and packs the response into ToolResult.

    Note: the adapter keeps a private ``_tool_name`` (the wire-level
    name the MCP server expects) separate from ``self.name`` (the
    Kairos-visible name, which the registry may namespace to avoid
    collisions). ``call_tool`` always uses ``_tool_name`` so the
    server routes correctly even when the Kairos name has been
    rewritten.
    """

    def __init__(self, client: StdioMcpClient, schema: Dict[str, Any]):
        self._client = client
        self._tool_name: str = schema.get("name", "mcp_unknown")
        # Default Kairos-visible name = wire name. The registry may
        # rewrite ``self.name`` after construction to namespace
        # colliding tools; that does NOT affect ``_tool_name``.
        self.name: str = self._tool_name
        self.description = (
            schema.get("description") or f"MCP tool {self._tool_name}"
        )
        self._input_schema = schema.get("inputSchema") or {
            "type": "object", "properties": {}
        }
        self._output_schema = schema.get("outputSchema")

    @property
    def parameters(self) -> Dict[str, Any]:
        return self._input_schema

    def to_schema(self) -> Dict[str, Any]:
        # OpenAI-style function schema
        out = {
            "name": self.name,
            "description": self.description,
            "parameters": self._input_schema,
        }
        # OpenAI strict mode requires additionalProperties: False.
        if isinstance(out["parameters"], dict):
            out["parameters"].setdefault("additionalProperties", False)
        return out

    async def execute(self, **kwargs: Any) -> ToolResult:
        # MCP requires arguments to be an object even when empty.
        if kwargs is None:
            kwargs = {}
        try:
            # Always call the wire-level tool name, not the
            # possibly-namespaced ``self.name``.
            result = await self._client.call_tool(
                self._tool_name, dict(kwargs)
            )
        except McpError as exc:
            return ToolResult(success=False, output="", error=str(exc))
        except Exception as exc:  # defensive
            return ToolResult(
                success=False, output="", error=f"unexpected MCP error: {exc}"
            )

        is_error = bool(result.get("isError"))
        content_blocks = result.get("content") or []
        text_pieces: List[str] = []
        other_meta: Dict[str, Any] = {}
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_pieces.append(str(block.get("text", "")))
            elif btype == "image":
                # Don't try to inline the bytes; record the metadata.
                other_meta.setdefault("images", []).append({
                    "mimeType": block.get("mimeType"),
                    "data_len": len(block.get("data") or ""),
                })
            elif btype == "resource":
                other_meta.setdefault("resources", []).append(block)
            else:
                other_meta.setdefault("other_blocks", []).append(block)
        text = "\n".join(p for p in text_pieces if p)
        # Truncate giant outputs to keep agent context sane.
        if len(text) > 50_000:
            text = text[:50_000] + "\n... (truncated)"
        return ToolResult(
            success=not is_error,
            output=text,
            error=("MCP tool reported error" if is_error else None),
            metadata={"tool": self._tool_name, **other_meta},
        )


# ---------------------------------------------------------------------------
# Registry: manage many clients, surface all tools as one catalog
# ---------------------------------------------------------------------------


class McpRegistry:
    """Loads MCP server configs, starts them, and aggregates their tools.

    Usage:
        registry = McpRegistry()
        await registry.start_all()
        try:
            tools = registry.all_tools()  # list[BaseTool]
            # pass to KairosAgent
        finally:
            await registry.close_all()
    """

    def __init__(self) -> None:
        self._clients: Dict[str, StdioMcpClient] = {}
        self._configs: Dict[str, McpServerConfig] = {}
        self._tools: Dict[str, McpToolAdapter] = {}
        self._startup_errors: Dict[str, str] = {}

    @property
    def startup_errors(self) -> Dict[str, str]:
        return dict(self._startup_errors)

    def load(
        self,
        project_dir: Optional[Path] = None,
        user_dir: Optional[Path] = None,
    ) -> None:
        """Load + parse YAML configs. Does not start subprocesses yet."""
        self._configs = load_configs(project_dir=project_dir, user_dir=user_dir)

    async def start_all(self) -> None:
        """Spawn subprocesses and complete initialize for every configured
        server. Servers that fail to start are recorded in
        `startup_errors` so callers can show a warning instead of
        crashing the whole registry."""
        for name, cfg in self._configs.items():
            if not cfg.enabled:
                continue
            client = client_for(cfg)
            try:
                await client.start()
            except Exception as exc:
                logger.warning("mcp: server %s failed to start: %s", name, exc)
                self._startup_errors[name] = str(exc)
                # Clean up partial state
                await client.close()
                continue
            self._clients[name] = client
            try:
                schemas = await client.list_tools()
            except Exception as exc:
                logger.warning(
                    "mcp: server %s tools/list failed: %s", name, exc
                )
                self._startup_errors[name] = f"tools/list: {exc}"
                await client.close()
                self._clients.pop(name, None)
                continue
            for schema in schemas:
                if not isinstance(schema, dict):
                    continue
                tname = schema.get("name")
                if not tname:
                    continue
                # Scope names so two servers exposing the same tool
                # name don't collide: mcp_<server>__<tool>.
                namespaced = f"mcp_{name}__{tname}"
                self._tools[namespaced] = McpToolAdapter(client, schema)
                # But BaseTool.name must be the namespaced one so the
                # LLM sees a unique identifier.
                self._tools[namespaced].name = namespaced
            logger.info(
                "mcp: server %s ready, %d tools", name, len(schemas)
            )

    def all_tools(self) -> List[BaseTool]:
        return list(self._tools.values())

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    async def close_all(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.close()
            except Exception as exc:
                logger.debug("mcp: close error: %s", exc)
        self._clients.clear()
        self._tools.clear()

    def __len__(self) -> int:
        return len(self._tools)


# ---------------------------------------------------------------------------
# Convenience: render a template YAML
# ---------------------------------------------------------------------------

TEMPLATE_YAML = """\
# Kairos MCP (Model Context Protocol) server list.
# All fields are optional. Servers with a missing command are skipped.
# See https://modelcontextprotocol.io for the protocol reference.

mcp_servers:
  # Example: GitHub MCP server (requires GITHUB_TOKEN env var).
  # Install with: npx -y @modelcontextprotocol/server-github
  # github:
  #   command: npx
  #   args: ["-y", "@modelcontextprotocol/server-github"]
  #   env:
  #     GITHUB_TOKEN: $${GITHUB_TOKEN}

  # Example: Postgres MCP server.
  # postgres:
  #   command: uvx
  #   args: ["mcp-server-postgres"]
  #   env:
  #     DATABASE_URL: postgres://user:pass@localhost/dbname

  # Disable a server without removing its config.
  # filesystem:
  #   command: npx
  #   args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
  #   enabled: false
"""
