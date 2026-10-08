"""Capability declaration and the capability gate for tools (P0-6).

A *capability* is a single, declarable bit saying what a tool does to the
world outside the model:

    READ_FILE       reads a file inside the tool's declared root
    WRITE_FILE      writes / patches a file inside the tool's declared root
    NETWORK         reaches the network
    EXEC_PROCESS    runs a process (shell, git, a child agent)
    EXTERNAL_WRITE  writes to a system that is not a file: a browser, the
                    desktop, a mail/drive/calendar API, an MCP server

The gate answers one question before a tool runs: **may this call, with
this target, proceed?** It is *fail-closed*: a tool that declares nothing
and is not registered is refused, not allowed.

How this stays compatible with the tools that already exist
----------------------------------------------------------
Existing tools are **explicitly registered** in :data:`TOOL_CAPABILITIES`
(chosen over deriving capabilities from name heuristics because a table is
deterministic, auditable and cannot silently mis-derive a bit for a tool
whose name is ambiguous -- e.g. ``history_search`` reads its own store, not
project files). For a registered tool the gate validates the *target* with
the very same helper the tool already uses -- ``BaseTool._resolve_safe`` for
paths, :mod:`kairos.netsec` for URLs -- and, when the target is fine, hands
the call through untouched. A refusal reuses the tool's own message, so the
observable behaviour of every existing tool is byte-for-byte what it is
today.

Adoption for new tools is by declaration:

* set ``capabilities`` on the tool class (``BaseTool``), or
* call :func:`register_tool_capabilities` (the registry path), or
* add an entry to :data:`TOOL_CAPABILITIES`.

Anything else -- a new tool that declares nothing -- is refused.

Approval reuses the existing ladder
-----------------------------------
Whether a declared capability needs the user's OK is decided by
:func:`requires_approval`, which mirrors :mod:`kairos.approval`'s
``SUGGEST`` / ``EDIT`` / ``FULL_AUTO`` semantics. ``KAIROS_APPROVAL_MODE``
stays a *process-wide ceiling*, not a default: the caller passes the
already-ceiling-clamped mode (see ``kairos.sentinel``).
"""
from __future__ import annotations

import enum
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, Optional, Tuple

from kairos.approval import ApprovalMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The bits
# ---------------------------------------------------------------------------


class Capability(str, enum.Enum):
    """A declarable bit describing a tool's side effects."""

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    NETWORK = "network"
    EXEC_PROCESS = "exec_process"
    EXTERNAL_WRITE = "external_write"


def _caps(*items: Capability) -> FrozenSet[Capability]:
    return frozenset(items)


# ---------------------------------------------------------------------------
# Explicit registration of the tools that already exist.
#
# This is the compatibility surface: every built-in tool appears here, so a
# registered tool keeps the behaviour it has today (the gate validates its
# target with the same helper it uses, then passes the call through). A tool
# missing from this table *and* declaring nothing is refused.
# ---------------------------------------------------------------------------

TOOL_CAPABILITIES: Dict[str, FrozenSet[Capability]] = {
    # -- reads inside the project root ------------------------------------
    "file_read": _caps(Capability.READ_FILE),
    "grep": _caps(Capability.READ_FILE),
    "find": _caps(Capability.READ_FILE),
    "code_search": _caps(Capability.READ_FILE),
    "history_search": _caps(Capability.READ_FILE),
    # -- writes inside the project root -----------------------------------
    "file_write": _caps(Capability.WRITE_FILE),
    "file_edit_replace": _caps(Capability.WRITE_FILE),
    "multi_edit": _caps(Capability.WRITE_FILE),
    "checkpoint": _caps(Capability.WRITE_FILE),
    # -- process execution ------------------------------------------------
    "terminal": _caps(Capability.EXEC_PROCESS),
    "git": _caps(Capability.EXEC_PROCESS),
    "spawn_subagent": _caps(Capability.EXEC_PROCESS),
    # -- in-memory / read-only bookkeeping --------------------------------
    "subagent_status": _caps(),
    "subagent_result": _caps(),
    "write_todos": _caps(),
    # -- network ----------------------------------------------------------
    "webfetch": _caps(Capability.NETWORK),
    "websearch": _caps(Capability.NETWORK),
    # -- external systems -------------------------------------------------
    "browser": _caps(Capability.NETWORK, Capability.EXTERNAL_WRITE),
    "computer_use": _caps(Capability.EXTERNAL_WRITE),
}

#: Names registered above -- the "legacy" set whose behaviour must not change.
LEGACY_TOOLS: FrozenSet[str] = frozenset(TOOL_CAPABILITIES)

#: MCP tools are namespaced ``mcp_<server>__<tool>`` and are created by the
#: ``McpToolAdapter``. They are third-party by nature and already governed by
#: the sentinel's taint rules, so they are declared *by convention* as
#: external writes rather than refused for being unknown.
MCP_PREFIX = "mcp_"
MCP_CAPABILITIES: FrozenSet[Capability] = _caps(Capability.EXTERNAL_WRITE)

#: Tools whose "network" argument names a URL we can check, and which guard to
#: use (kept in step with the tool itself, never a second opinion):
#:   strict  -- kairos.netsec.validate_public_url   (webfetch)
#:   lenient -- kairos.netsec.validate_config_url   (browser: loopback/LAN ok)
#:   none    -- the URL is a server-side constant, not attacker-controlled
#:              (websearch's configured API endpoint, like the ollama preset)
NETWORK_GUARD: Dict[str, str] = {
    "webfetch": "strict",
    "browser": "lenient",
    "websearch": "none",
}

#: Argument names that carry a path for a file-capability tool. Kept narrow on
#: purpose: only the keys the file tools actually use, so the fence never
#: second-guesses an unrelated argument.
PATH_ARG_KEYS: Tuple[str, ...] = ("path", "file_path")

#: Argument names worth putting in an audit target, most specific first.
_TARGET_KEYS: Tuple[str, ...] = (
    "url", "command", "path", "file_path", "subcommand", "query", "label", "task",
)


# ---------------------------------------------------------------------------
# Runtime registry -- how a *new* tool declares itself by name, and how the
# sentinel (which only ever sees a tool's name) learns the declaration.
# ---------------------------------------------------------------------------

_RUNTIME: Dict[str, FrozenSet[Capability]] = {}


def register_tool_capabilities(
    name: str, capabilities: Iterable[Capability]
) -> FrozenSet[Capability]:
    """Declare capabilities for a tool by name (the registry path)."""
    caps = frozenset(Capability(c) for c in capabilities)
    _RUNTIME[str(name)] = caps
    return caps


def runtime_capabilities(name: str) -> Optional[FrozenSet[Capability]]:
    """Capabilities declared at runtime for ``name``, or ``None``.

    Used by the sentinel to tell "a new tool declared through the gate" (which
    is subject to capability approval) from a registered/legacy tool (whose
    behaviour must not change).
    """
    return _RUNTIME.get(str(name))


def clear_runtime_capabilities() -> None:
    """Drop every runtime declaration (test hygiene)."""
    _RUNTIME.clear()


def unregister_tool_capabilities(name: str) -> None:
    """Drop one runtime declaration (test hygiene)."""
    _RUNTIME.pop(str(name), None)


def resolve_capabilities(
    name: str, obj: Any = None
) -> Optional[FrozenSet[Capability]]:
    """The capability set for a call, or ``None`` when it is undeclared.

    Order: the tool's own class declaration, then a runtime registration, then
    the built-in table, then the ``mcp_`` convention. ``None`` is the
    fail-closed signal the gate refuses on.
    """
    if obj is not None:
        declared = getattr(obj, "capabilities", None)
        if declared is not None:
            return frozenset(Capability(c) for c in declared)
    key = (name or "").strip().lower()
    if key in _RUNTIME:
        return _RUNTIME[key]
    if key in TOOL_CAPABILITIES:
        return TOOL_CAPABILITIES[key]
    if key.startswith(MCP_PREFIX):
        return MCP_CAPABILITIES
    return None


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def _cap_values(caps: Iterable[Capability]) -> list:
    return sorted(c.value for c in caps)


def target_of(args: Any) -> str:
    """The most useful single string describing what a call touches."""
    if isinstance(args, str):
        return args[:400]
    if not isinstance(args, dict):
        return ""
    for key in _TARGET_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:400]
    return ""


@dataclass(frozen=True)
class CapabilityVerdict:
    """The gate's verdict on one call. ``allowed`` is the whole answer."""

    tool: str
    capabilities: FrozenSet[Capability]
    target: str
    allowed: bool
    reason: str
    rule: str
    error_text: str = ""

    def message(self) -> str:
        if self.allowed:
            return ""
        if self.error_text:
            return self.error_text
        return f"Refused by the capability gate: {self.reason}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "capabilities": _cap_values(self.capabilities),
            "target": self.target,
            "decision": "allow" if self.allowed else "deny",
            "rule": self.rule,
            "reason": self.reason,
        }


def _verdict(tool: str, caps: FrozenSet[Capability], args: Any, allowed: bool,
             reason: str, rule: str, error_text: str = "") -> CapabilityVerdict:
    return CapabilityVerdict(
        tool=tool, capabilities=caps, target=target_of(args),
        allowed=allowed, reason=reason, rule=rule, error_text=error_text,
    )


# ---------------------------------------------------------------------------
# The target checks
# ---------------------------------------------------------------------------


def fence_path(path: str, root: Any, obj: Any = None) -> Path:
    """Resolve ``path`` within ``root``, or raise ``PermissionError``.

    No second path policy: when the tool object is available we call its own
    :meth:`BaseTool._resolve_safe`, so a refusal is the tool's own message.
    Without an object we call the shared :func:`resolve_within_root` the tool
    itself delegates to.
    """
    resolver = getattr(obj, "_resolve_safe", None)
    if callable(resolver):
        return resolver(path)
    from kairos.tools.base import resolve_within_root
    if root is None:
        raise PermissionError("no allowed root to fence against")
    return resolve_within_root(path, Path(root))


def _check_paths(tool: str, caps: FrozenSet[Capability], args: Dict[str, Any],
                 obj: Any, root: Any) -> Optional[CapabilityVerdict]:
    if not (caps & {Capability.READ_FILE, Capability.WRITE_FILE}):
        return None
    for key in PATH_ARG_KEYS:
        value = args.get(key)
        if not (isinstance(value, str) and value):
            continue
        try:
            fence_path(value, root, obj=obj)
        except PermissionError as exc:
            return _verdict(tool, caps, args, False, str(exc), "path-fence",
                            error_text=str(exc))
    return None


def _check_network(tool: str, caps: FrozenSet[Capability],
                   args: Dict[str, Any]) -> Optional[CapabilityVerdict]:
    if Capability.NETWORK not in caps:
        return None
    url = args.get("url")
    if not (isinstance(url, str) and url):
        return None
    key = (tool or "").strip().lower()
    guard = NETWORK_GUARD.get(key, "strict")
    if guard == "none":
        return None
    # A browser only fetches for the acting navigation actions; a screenshot or
    # a console dump has no URL to guard.
    if key == "browser":
        action = str(args.get("action") or "").strip().lower()
        if not action and url:
            action = "navigate"
        if action not in ("open", "navigate"):
            return None
    # A non-http(s) URL is a different error ("url must be http(s)"), reported
    # by the tool itself; do not shadow it.
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
    try:
        if guard == "lenient":
            from kairos.netsec import validate_config_url as guard_fn
        else:
            from kairos.netsec import validate_public_url as guard_fn
    except Exception as exc:  # noqa: BLE001 - guard missing => fail closed
        return _verdict(tool, caps, args, False,
                        f"network guard unavailable ({exc}); refusing (fail-closed)",
                        "network-guard-unavailable")
    try:
        guard_fn(url, what="url")
    except ValueError as exc:
        return _verdict(tool, caps, args, False, str(exc), "network-guard",
                        error_text=str(exc))
    return None


# ---------------------------------------------------------------------------
# Approval (mirrors kairos.approval's ladder)
# ---------------------------------------------------------------------------

#: Reading a file inside the root is the only fully silent capability.
READ_ONLY_CAPS: FrozenSet[Capability] = _caps(Capability.READ_FILE)
#: What SUGGEST's "trivial" and EDIT's "automatic" tiers cover, kept in step
#: with ``kairos.approval.READ_ONLY_TOOLS`` (which includes the network read).
EDIT_SILENT_CAPS: FrozenSet[Capability] = _caps(
    Capability.READ_FILE, Capability.WRITE_FILE, Capability.NETWORK,
)


def requires_approval(
    caps: FrozenSet[Capability], mode: ApprovalMode
) -> Tuple[bool, str]:
    """Does a declared capability need the user's OK in ``mode``?

    The mode is the *effective* mode (``KAIROS_APPROVAL_MODE`` as a ceiling
    already applied by the sentinel). FULL_AUTO silences everything; a
    read-only capability is always silent; EDIT auto-allows file writes and
    network reads (the same set ``kairos.approval`` treats as trivial).
    Everything else -- process execution, external writes -- asks unless the
    mode is FULL_AUTO.
    """
    if not caps:
        return False, "no capability"
    if mode == ApprovalMode.FULL_AUTO:
        return False, "full-auto: nothing asks"
    if caps <= READ_ONLY_CAPS:
        return False, "read-only capability"
    if mode == ApprovalMode.EDIT and caps <= EDIT_SILENT_CAPS:
        return False, "edit mode: file writes and network reads are automatic"
    return True, (f"capability {_cap_values(caps)} needs approval "
                  f"in mode={mode.value}")


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def assess(tool: str, args: Any, *, obj: Any = None, root: Any = None
           ) -> CapabilityVerdict:
    """Decide whether a call may run, judged on capability + target.

    Fail-closed: an undeclared tool is refused. A declared tool has each of
    its declared capabilities checked against its target -- paths are fenced
    within the declared root, network targets go through the existing SSRF
    guard -- then, if nothing objected, the call is allowed.
    """
    caps = resolve_capabilities(tool, obj)
    if caps is None:
        return CapabilityVerdict(
            tool=tool, capabilities=frozenset(), target=target_of(args),
            allowed=False,
            reason="tool declares no capability set; refusing (fail-closed)",
            rule="undeclared-capability",
            error_text=(
                f"Refused by the capability gate: {tool!r} declares no "
                f"capability set, so it may not run. Register it "
                f"(register_tool_capabilities) or set its `capabilities` "
                f"attribute."),
        )
    norm = args if isinstance(args, dict) else {}
    objection = _check_paths(tool, caps, norm, obj, root)
    if objection is None:
        objection = _check_network(tool, caps, norm)
    if objection is not None:
        return objection
    return _verdict(tool, caps, norm, True, "capability and target ok",
                    "capability-allow")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit_dir() -> Path:
    """Where the capability gate writes its trail (never user project data)."""
    override = os.environ.get("KAIROS_CAPABILITY_AUDIT_DIR")
    if override:
        return Path(override)
    from kairos.sentinel import sentinel_dir
    return sentinel_dir()


def audit_file() -> Path:
    return audit_dir() / "capabilities.jsonl"


def _redact(text: str) -> str:
    """Strip credential-shaped substrings before anything is stored."""
    try:
        from kairos.sentinel import redact
        return redact(text)
    except Exception:  # noqa: BLE001
        return text


def audit(verdict: CapabilityVerdict, *, actor: str = "") -> None:
    """Append one readable line: who / tool / capability / target / result.

    Best-effort and never raises -- a failed audit must not change a ruling.
    The target and reason are redacted so a key or token can never be logged.
    """
    if os.environ.get("KAIROS_CAPABILITY_AUDIT", "").strip().lower() in (
            "0", "off", "false", "no"):
        return
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "actor": actor or os.environ.get("KAIROS_AGENT_ID", "") or "agent",
        "tool": verdict.tool,
        "capability": _cap_values(verdict.capabilities),
        "target": _redact(verdict.target),
        "result": "allow" if verdict.allowed else "deny",
        "rule": verdict.rule,
        "reason": _redact(verdict.reason),
    }
    try:
        path = audit_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("capability audit write failed: %s", exc)
