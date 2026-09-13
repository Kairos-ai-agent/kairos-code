"""Hook system for Kairos.

This subpackage exposes two complementary hook systems:

  1. **Programmatic registry** (``HookRegistry``) — the rich
     YAML-configurable system added in Round 5. Supports
     ``PreToolUse`` / ``PostToolUse`` / ``Stop`` events with
     command, python-module, and built-in Python hooks.

  2. **Data-directory hook files** (``HookRunner``) — the original
     system from earlier rounds. Users drop Python files into
     ``data/hooks/*.py`` defining ``pre_tool_use``,
     ``post_tool_use``, ``loop_round``, ``loop_completed``
     functions. Re-exported here for backwards compatibility.

Most new code should use the registry (system 1) — it has better
typing, async support, YAML config, and per-hook error handling.
The data-dir system is kept for users who already have hook files.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import yaml

logger = logging.getLogger(__name__)


# Re-export the old data-dir hook system for backwards compatibility.
from kairos.hooks.runner import HookRunner, get_runner  # noqa: F401


__all__ = [
    # Old data-dir system
    "HookRunner",
    "get_runner",
    # New registry system
    "HookEvent",
    "HookDecision",
    "HookContext",
    "HookResult",
    "HookSpec",
    "HookRegistry",
    "get_default_registry",
    "reset_default_registry",
    "load_project_hooks",
]


# ---------------------------------------------------------------------------
# New registry-based system
# ---------------------------------------------------------------------------


class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"


class HookDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


@dataclass
class HookContext:
    """Inputs and outputs passed to a hook.

    For ``PreToolUse`` / ``PostToolUse``: ``tool_name`` and
    ``tool_input`` (and ``tool_output`` for Post) are populated.

    For ``SessionStart``: ``tool_name`` is empty; ``metadata`` may
    contain the new session id and the requirement string.

    For ``SessionEnd`` / ``Stop``: ``tool_name`` is empty;
    ``metadata`` carries the final score, total rounds, and
    outcome gate name.
    """
    event: HookEvent
    project_id: str
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)
    tool_output: Optional[Any] = None
    tool_error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """What a hook decided.

    For ``PreToolUse``:
      - ``decision == ALLOW`` (default)  — let the tool run as-is
      - ``decision == DENY``             — abort the tool call
      - ``decision == MODIFY``           — replace tool_input with
                                          ``modified_input`` and run
                                          the new version
    For ``PostToolUse`` / ``Stop``: ``decision`` is ignored; only
    ``modified_output`` is read.
    """
    decision: HookDecision = HookDecision.ALLOW
    reason: str = ""
    modified_input: Optional[Dict[str, Any]] = None
    modified_output: Optional[Any] = None
    message: str = ""


# A Python hook is just an async (or sync) callable.
PythonHook = Callable[[HookContext], Union[HookResult, Awaitable[HookResult]]]


@dataclass
class HookSpec:
    """A single configured hook."""
    event: HookEvent
    matcher: Optional[str]
    hook_type: str  # "command" | "python" | "builtin"
    command: str = ""
    module: str = ""
    function: str = ""
    builtin: Optional[PythonHook] = None
    on_error: str = "allow"
    timeout_s: float = 10.0

    def matches(self, tool_name: str) -> bool:
        # Session lifecycle events have no tool name; if the hook
        # is bound to one of them, the matcher is irrelevant.
        if self.event in (HookEvent.SESSION_START, HookEvent.SESSION_END,
                          HookEvent.STOP):
            return True
        if self.matcher is None:
            return True
        return bool(re.search(self.matcher, tool_name))


class HookRegistry:
    """Central registry of hooks. The orchestrator queries this on
    every tool call.
    """

    def __init__(self) -> None:
        self._hooks: Dict[HookEvent, List[HookSpec]] = {e: [] for e in HookEvent}

    # -- registration ----------------------------------------------------

    def register(self, spec: HookSpec) -> None:
        if not isinstance(spec, HookSpec):
            raise TypeError("expected HookSpec")
        self._hooks[spec.event].append(spec)

    def register_builtin(self, event: HookEvent, fn: PythonHook,
                         *, matcher: Optional[str] = None,
                         on_error: str = "allow") -> None:
        self.register(HookSpec(
            event=event, matcher=matcher, hook_type="builtin",
            builtin=fn, on_error=on_error,
        ))

    def hooks_for(self, event: HookEvent, tool_name: str = "") -> List[HookSpec]:
        return [h for h in self._hooks[event] if h.matches(tool_name)]

    def clear(self) -> None:
        for k in self._hooks:
            self._hooks[k].clear()

    def all_specs(self) -> List[HookSpec]:
        out: List[HookSpec] = []
        for specs in self._hooks.values():
            out.extend(specs)
        return out

    # -- loading from YAML ----------------------------------------------

    def load_yaml(self, path) -> int:
        p = Path(path)
        if not p.is_file():
            return 0
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            logger.warning("hooks: bad YAML in %s: %s", p, exc)
            return 0
        if not isinstance(data, dict):
            return 0
        return self.load_dict(data)

    def load_dict(self, data: Dict[str, Any]) -> int:
        count = 0
        hooks = data.get("hooks") or {}
        if not isinstance(hooks, dict):
            return 0
        for event_name, specs in hooks.items():
            try:
                event = HookEvent(event_name)
            except ValueError:
                logger.warning("hooks: unknown event %r; skipping", event_name)
                continue
            if not isinstance(specs, list):
                continue
            for spec in specs:
                if not isinstance(spec, dict):
                    continue
                try:
                    self.register(self._spec_from_dict(event, spec))
                    count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("hooks: bad spec %r: %s", spec, exc)
        return count

    def _spec_from_dict(self, event: HookEvent, d: Dict[str, Any]) -> HookSpec:
        hook_type = str(d.get("type") or "command").lower()
        if hook_type not in ("command", "python", "builtin"):
            raise ValueError(f"unknown hook type: {hook_type}")
        return HookSpec(
            event=event,
            matcher=d.get("matcher"),
            hook_type=hook_type,
            command=str(d.get("command") or ""),
            module=str(d.get("module") or ""),
            function=str(d.get("function") or ""),
            on_error=str(d.get("on_error") or "allow"),
            timeout_s=float(d.get("timeout_s") or 10.0),
        )

    # -- execution -------------------------------------------------------

    async def run(self, ctx: HookContext) -> HookResult:
        result = HookResult()
        for spec in self.hooks_for(ctx.event, ctx.tool_name):
            single = await self._run_one(spec, ctx)
            if single is None:
                if spec.on_error == "deny":
                    return HookResult(
                        decision=HookDecision.DENY,
                        reason=f"hook {spec.matcher or '*'} failed",
                    )
                continue
            if single.decision == HookDecision.DENY:
                return single
            if single.decision == HookDecision.MODIFY:
                result = single
                continue
            if single.message:
                result.message = (result.message + "\n" + single.message).strip()
        return result

    async def _run_one(self, spec: HookSpec, ctx: HookContext) -> Optional[HookResult]:
        try:
            if spec.hook_type == "command":
                return await self._run_command(spec, ctx)
            if spec.hook_type == "python":
                return await self._run_python(spec, ctx)
            if spec.hook_type == "builtin":
                return await self._run_builtin(spec, ctx)
        except Exception:
            logger.exception("hook %r failed", spec.matcher)
            return None
        return None

    async def _run_command(self, spec: HookSpec, ctx: HookContext) -> HookResult:
        env = self._env_for(ctx)
        try:
            proc = await asyncio.create_subprocess_shell(
                spec.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                # Give the hook its own process group. Without this it inherits
                # ours, and the POSIX branch of _force_kill below kills that group
                # — i.e. the hook timing out would SIGKILL the test run (or the
                # whole CI job) from the inside: exit 137, no traceback, nothing to
                # diagnose with. Windows uses taskkill and has no groups, so this
                # stays POSIX-only.
                **({"start_new_session": True} if os.name == "posix" else {}),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=spec.timeout_s,
            )
        except asyncio.TimeoutError:
            await self._force_kill(proc)
            logger.warning("hook command timed out after %.1fs: %s",
                           spec.timeout_s, spec.command[:200])
            return HookResult()
        except Exception as exc:
            logger.warning("hook command failed to start: %s", exc)
            return HookResult()

        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        decision = HookDecision.DENY if proc.returncode != 0 else HookDecision.ALLOW
        return HookResult(
            decision=decision,
            reason=f"exit={proc.returncode}",
            message=(out + ("\n[stderr] " + err if err else "")).strip(),
        )

    async def _run_python(self, spec: HookSpec, ctx: HookContext) -> HookResult:
        import importlib
        try:
            mod = importlib.import_module(spec.module)
            fn = getattr(mod, spec.function)
        except (ImportError, AttributeError) as exc:
            logger.warning("python hook import failed: %s", exc)
            return HookResult()
        result = fn(ctx)
        if hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, HookResult):
            return HookResult()
        return result

    async def _run_builtin(self, spec: HookSpec, ctx: HookContext) -> HookResult:
        if spec.builtin is None:
            return HookResult()
        result = spec.builtin(ctx)
        if hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, HookResult):
            return HookResult()
        return result

    def _env_for(self, ctx: HookContext) -> Dict[str, str]:
        out = dict(os.environ)
        out["KAIROS_EVENT"] = ctx.event.value
        out["KAIROS_PROJECT_ID"] = ctx.project_id
        out["TOOL_NAME"] = ctx.tool_name
        out["TOOL_INPUT"] = json.dumps(ctx.tool_input, ensure_ascii=False)
        out["TOOL_INPUT_JSON"] = out["TOOL_INPUT"]
        if ctx.tool_output is not None:
            try:
                out["TOOL_OUTPUT"] = json.dumps(ctx.tool_output, ensure_ascii=False)
            except TypeError:
                out["TOOL_OUTPUT"] = str(ctx.tool_output)
        if ctx.tool_error:
            out["TOOL_ERROR"] = ctx.tool_error
        return out

    @staticmethod
    async def _force_kill(proc) -> None:
        """Best-effort kill that works on both POSIX and Windows.

        On Windows, ``proc.kill()`` only sends ``TerminateProcess`` to
        the immediate child, which leaves grandchildren alive. We
        use ``taskkill /T /F /PID`` to recursively kill the tree.
        """
        try:
            if os.name == "nt":
                # taskkill is built-in on Windows. /T = tree, /F = force.
                tk = await asyncio.create_subprocess_exec(
                    "taskkill", "/T", "/F", "/PID", str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(tk.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
            else:
                # POSIX: kill the hook's own process group, and never our own. If
                # a caller forgot start_new_session the child shares this process's
                # group, and killpg would then SIGKILL the test run — exit 137 with
                # no traceback, which is exactly how this hid for so long.
                try:
                    import signal
                    child_pgid = os.getpgid(proc.pid)
                    if child_pgid == os.getpgid(0):
                        proc.kill()
                    else:
                        os.killpg(child_pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass


# ---------------------------------------------------------------------------
# Default registry singleton + convenience loaders
# ---------------------------------------------------------------------------


_global_registry: Optional[HookRegistry] = None


def get_default_registry() -> HookRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = HookRegistry()
    return _global_registry


def reset_default_registry() -> None:
    global _global_registry
    _global_registry = None


def load_project_hooks(project_dir, user_dir=None) -> int:
    """Load hooks from ``<project>/.kairos/hooks.yaml`` +
    ``<user>/hooks.yaml`` (project wins on order).
    """
    reg = get_default_registry()
    count = 0
    if user_dir:
        count += reg.load_yaml(Path(user_dir) / "hooks.yaml")
    if project_dir:
        count += reg.load_yaml(Path(project_dir) / ".kairos" / "hooks.yaml")
    return count
