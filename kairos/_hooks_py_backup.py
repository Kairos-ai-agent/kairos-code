"""Hooks system for Kairos.

Mirrors Claude Code's lifecycle hook model. Three event types:

  - ``PreToolUse``   — fires before a tool runs. The hook can
    ``allow``, ``deny``, or ``modify`` the call. Returning ``deny``
    aborts the tool; the agent sees an error.
  - ``PostToolUse``  — fires after a tool runs. Hooks can log,
    transform the result, or trigger side effects (e.g. re-format
    files after a ``file_edit``).
  - ``Stop``         — fires when an agent loop ends. Hooks can
    emit a final summary, run a verifier, or decide whether to
    continue.

Hooks are configured in YAML::

    # .kairos/hooks.yaml
    hooks:
      PreToolUse:
        - matcher: "terminal"
          type: command
          command: "echo 'about to run: $TOOL_INPUT'"
      PostToolUse:
        - matcher: "file_edit"
          type: command
          command: "ruff format $FILE"
      Stop:
        - type: command
          command: "echo 'session done'"

Three hook types are supported:

  - ``command`` — runs a shell command. The hook receives a
    environment with ``$TOOL_NAME``, ``$TOOL_INPUT``, ``$TOOL_OUTPUT``,
    ``$PROJECT_ID``, etc.
  - ``python`` — imports a function from a module and calls it.
  - ``builtin`` — registers a Python callable directly via the API.

The runner is safe-by-default: a hook that fails (non-zero exit,
import error, exception) is logged and the original tool call
proceeds. ``PreToolUse`` can be configured to *block* the tool on
failure (``on_error: deny``) when security matters.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Union

import yaml

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"


class HookDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


@dataclass
class HookContext:
    """Inputs and outputs passed to a hook."""
    event: HookEvent
    project_id: str
    tool_name: str
    tool_input: Dict[str, Any] = field(default_factory=dict)
    tool_output: Optional[Any] = None  # only set on PostToolUse
    tool_error: Optional[str] = None   # only set on PostToolUse if the tool failed
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
    # When non-empty, this text is appended to the agent's
    # transcript ("Hook said: ...").
    message: str = ""


# A Python hook is just an async (or sync) callable.
PythonHook = Callable[[HookContext], Union[HookResult, Awaitable[HookResult]]]


@dataclass
class HookSpec:
    """A single configured hook."""
    event: HookEvent
    matcher: Optional[str]  # tool name regex, or None to match all
    hook_type: str  # "command" | "python" | "builtin"
    command: str = ""
    module: str = ""
    function: str = ""
    builtin: Optional[PythonHook] = None
    on_error: str = "allow"  # what to do if the hook itself fails: allow | deny
    timeout_s: float = 10.0

    def matches(self, tool_name: str) -> bool:
        if self.matcher is None:
            return True
        return bool(re.search(self.matcher, tool_name))


# ---------------------------------------------------------------------------
# Hook registry
# ---------------------------------------------------------------------------


class HookRegistry:
    """Central registry of hooks. The orchestrator queries this on
    every tool call.
    """

    def __init__(self) -> None:
        self._hooks: Dict[HookEvent, List[HookSpec]] = {e: [] for e in HookEvent}
        self._python_hooks: Dict[str, PythonHook] = {}

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

    def load_yaml(self, path: Union[str, Path]) -> int:
        """Load hooks from a YAML file. Returns the count loaded.

        Format::

            hooks:
              PreToolUse:
                - matcher: "terminal"   # optional
                  type: command
                  command: "echo $TOOL_INPUT"
                  on_error: deny
              PostToolUse:
                - type: python
                  module: mypkg.hooks
                  function: reformat
        """
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
        """Load hooks from a dict (e.g. the parsed YAML)."""
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
        """Run all matching hooks and merge their decisions.

        Merging rules:
          - First DENY wins, the rest are skipped.
          - Otherwise the last MODIFY's modified_input is used.
          - The first ALLOW (or any no-op) is the default.
        """
        result = HookResult()
        for spec in self.hooks_for(ctx.event, ctx.tool_name):
            single = await self._run_one(spec, ctx)
            if single is None:
                # hook errored — apply on_error policy
                if spec.on_error == "deny":
                    return HookResult(
                        decision=HookDecision.DENY,
                        reason=f"hook {spec.matcher or '*'} failed",
                    )
                continue
            # First DENY wins.
            if single.decision == HookDecision.DENY:
                return single
            # Keep the most recent MODIFY.
            if single.decision == HookDecision.MODIFY:
                result = single
                continue
            # Carry forward messages even on ALLOW so the agent can
            # see what the hook did.
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
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=spec.timeout_s,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            logger.warning("hook command timed out after %.1fs: %s",
                           spec.timeout_s, spec.command[:200])
            return HookResult()
        except Exception as exc:
            logger.warning("hook command failed to start: %s", exc)
            return HookResult()

        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        # We use exit code to decide ALLOW vs DENY for PreToolUse.
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
        # Allow both sync and async hooks.
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
        """Build the env passed to a command hook.

        Exposes the common fields as both $TOOL_NAME and structured
        JSON in $TOOL_INPUT_JSON, so shells can use either.
        """
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_modify(modify_str: str, ctx: HookContext) -> Optional[Dict[str, Any]]:
    """Parse a hook's ``--modify '{...}'`` style output.

    Used when a command hook wants to return a JSON patch. We
    accept either:
      - ``modify: {"key": "value"}`` (line-based)
      - a single JSON object on stdout
    """
    s = (modify_str or "").strip()
    if not s:
        return None
    if s.startswith("modify:"):
        s = s[len("modify:"):].strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


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


def load_project_hooks(project_dir: Union[str, Path],
                       user_dir: Optional[Union[str, Path]] = None) -> int:
    """Load hooks from ``<project>/.kairos/hooks.yaml`` +
    ``<user>/hooks.yaml`` (project wins on conflicts because it is
    loaded second and the registry is order-preserving).
    """
    reg = get_default_registry()
    count = 0
    if user_dir:
        count += reg.load_yaml(Path(user_dir) / "hooks.yaml")
    if project_dir:
        count += reg.load_yaml(Path(project_dir) / ".kairos" / "hooks.yaml")
    return count
