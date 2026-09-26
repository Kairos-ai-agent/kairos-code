"""Which content in this run the agent cannot vouch for.

Taken from the idea behind Muse's "tainted egress": judge an action not only by
what it is, but by what the agent read before it. Muse taints a run when the agent
touches *user* data, because its action space is payments and email -- any read
can be followed by a send.

A coding agent reads the repository it was asked to edit, so tainting on local
reads would deny every write in every run and teach the user to switch the gate
off. This module therefore calibrates to a coding agent: what taints a run is
content that crossed a trust boundary --

  * a tool that reaches the network (a docs page, a search result, a tarball), and
  * a third-party MCP server's output: code this project did not write, whose
    output can be steered by anything that server reads, and
  * the user's screen (``computer_use``): pixels are written by whatever
    application happens to be open, which is exactly the "can be steered by
    something else" property that makes a page untrusted.

Local reads and writes stay untainted -- that is the job the agent was given.

A :class:`TaintTracker` belongs to one run (one agent). :meth:`mark` records why,
and :meth:`sources` returns those reasons, so a later denial can name the page or
the server that caused it instead of being mysterious. Tracking must survive the
agent's tool calls, which may run on a worker thread, hence the lock.
"""
from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Tool names that reach the network. Prefix-matched, so ``webfetch`` and
# ``webfetch_url`` are both covered.
NETWORK_TOOL_PREFIXES: Tuple[str, ...] = (
    "webfetch", "web_fetch", "webfetch_url", "fetch_url", "http_", "httprequest",
    "http_request", "websearch", "web_search", "browse", "browser_", "search_web",
    "market_", "install_extension", "extensions_install", "download",
)

# Tools that read the user's screen. A screen is an untrusted source for the
# same reason a web page is: whatever is on it was put there by something else,
# and a model that has read it can be steered by it. Prefix-matched like the
# network set so ``computer_use`` and any future ``computer_use_*`` are covered.
SCREEN_TOOL_PREFIXES: Tuple[str, ...] = (
    "computer_use", "computer-use", "screen_capture", "screenshot",
)

# MCP tools are namespaced ``mcp_<server>__<tool>`` (see mcp_client).
MCP_PREFIX = "mcp_"
MCP_SEPARATOR = "__"

# Servers this project ships are our own code. A third-party server (installed by
# the user, or by a marketplace) is not -- and treating the two alike would taint
# every run that touched a bundled server, which is every run.
TRUSTED_MCP_SOURCES = ("bundled-plugin", "bundled")


@dataclass(frozen=True)
class TaintSource:
    """One reason the run is tainted."""

    kind: str          # "network" | "mcp"
    tool: str          # the tool call that brought the content in
    detail: str = ""   # server name for mcp, or a short note
    at: float = field(default_factory=time.time)

    def describe(self) -> str:
        if self.kind == "mcp" and self.detail:
            return f"MCP server {self.detail!r} via {self.tool}"
        if self.kind == "screen":
            return f"{self.tool} (the user's screen)"
        return f"{self.tool} (network)"


def is_mcp_tool(name: str) -> bool:
    return bool(name) and name.startswith(MCP_PREFIX)


def mcp_server_of(name: str) -> str:
    """``mcp_github__list_issues`` -> ``github`` (empty when unparseable)."""
    if not is_mcp_tool(name):
        return ""
    rest = name[len(MCP_PREFIX):]
    server, _, _ = rest.partition(MCP_SEPARATOR)
    return server


def is_network_tool(name: str) -> bool:
    lowered = (name or "").lower()
    if is_mcp_tool(lowered):
        return False
    return any(lowered.startswith(p) for p in NETWORK_TOOL_PREFIXES)


def is_screen_tool(name: str) -> bool:
    lowered = (name or "").lower()
    if is_mcp_tool(lowered):
        return False
    return any(lowered.startswith(p) for p in SCREEN_TOOL_PREFIXES)


def classify(name: str, tool: Any = None) -> Optional[str]:
    """Return the taint kind a call to ``name`` produces, or None.

    MCP wins over the network check: a tool from a third-party server is
    untrusted for a stronger reason than one that merely fetches a URL. Pass the
    tool object when it is available so a server this project ships can be
    recognised and skipped.
    """
    if is_mcp_tool(name or ""):
        if getattr(tool, "mcp_source", "") in TRUSTED_MCP_SOURCES:
            return None
        return "mcp"
    if is_screen_tool(name or ""):
        return "screen"
    if is_network_tool(name or ""):
        return "network"
    return None


# The tracker of the run currently executing. A subagent is constructed inside a
# tool call, so it reads this and inherits the parent's provenance instead of
# starting clean -- otherwise fan-out would be a way around the gate.
_CURRENT: "contextvars.ContextVar[Optional[TaintTracker]]" = contextvars.ContextVar(
    "kairos_taint", default=None
)


def current_tracker() -> Optional["TaintTracker"]:
    return _CURRENT.get()


def use_tracker(tracker: "TaintTracker"):
    """Make ``tracker`` the run's tracker for the calling context."""
    return _CURRENT.set(tracker)


def release_tracker(token) -> None:
    try:
        _CURRENT.reset(token)
    except Exception:  # noqa: BLE001
        pass


class TaintTracker:
    """Per-run record of untrusted provenance."""

    def __init__(self, origin: str = "") -> None:
        self._lock = threading.Lock()
        self._sources: List[TaintSource] = []
        self.origin = origin

    def mark(self, kind: str, tool: str, detail: str = "") -> TaintSource:
        """Record that ``tool`` brought untrusted content into the run."""
        source = TaintSource(kind=kind, tool=tool, detail=detail)
        with self._lock:
            # One entry per (kind, tool, detail): a page fetched ten times is
            # one reason, not ten.
            for existing in self._sources:
                if (existing.kind, existing.tool, existing.detail) == \
                        (source.kind, source.tool, source.detail):
                    return existing
            self._sources.append(source)
        return source

    def mark_tool(self, name: str, tool: Any = None) -> Optional[TaintSource]:
        """Classify and mark in one step. Returns None for trusted tools."""
        kind = classify(name, tool)
        if kind is None:
            return None
        return self.mark(kind, name, mcp_server_of(name) if kind == "mcp" else "")

    @property
    def tainted(self) -> bool:
        with self._lock:
            return bool(self._sources)

    def sources(self) -> List[TaintSource]:
        with self._lock:
            return list(self._sources)

    def describe(self, limit: int = 3) -> str:
        items = [s.describe() for s in self.sources()]
        if not items:
            return "no untrusted content in this run"
        shown = items[:limit]
        extra = f" (+{len(items) - len(shown)} more)" if len(items) > len(shown) else ""
        return "; ".join(shown) + extra

    def snapshot(self) -> Dict[str, object]:
        return {
            "tainted": self.tainted,
            "sources": [
                {"kind": s.kind, "tool": s.tool, "detail": s.detail} for s in self.sources()
            ],
        }

    def clear(self) -> None:
        """Forget the taint. Called when a new run starts, never by the agent."""
        with self._lock:
            self._sources.clear()
