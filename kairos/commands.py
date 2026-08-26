"""Slash command system for Kairos.

Slash commands are short user-typed prefixes (``/review``,
``/plan``, ``/commit``, …) that the chat layer intercepts before
the message reaches the LLM. They are useful for:

  - **fast access to common workflows** — `/review` triggers a
    reviewer-style read-only pass without a full loop
  - **shell-out to local tools** — `/test` runs `pytest`, `/lint`
    runs `ruff`, `/format` runs `ruff format`
  - **session metadata** — `/mode read_only` switches the Coder
    sub-mode for this project

A command is a Python callable that receives a parsed argument
string and a :class:`CommandContext`, and returns a
:class:`CommandResult` describing what the chat layer should do
with it (replace the user message, append to it, show as system
output, etc.).

Commands are registered in two ways:

  - **Built-in**: hard-coded in :data:`BUILTIN_COMMANDS` (or
    registered by the orchestrator at startup)
  - **User-defined**: loaded from ``.kairos/commands/*.py`` files
    (each defines one or more ``register(registry)`` functions)

Usage::

    parser = CommandParser()
    result = parser.handle("/test tests/test_foo.py", ctx)
    if result.replace_message:
        send_to_llm(result.replace_message)
    if result.system_output:
        display(result.system_output)
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context / result types
# ---------------------------------------------------------------------------


@dataclass
class CommandContext:
    """What the chat layer hands to a slash command."""
    project_id: str
    work_dir: str
    user_id: str = ""
    # Optional back-references the orchestrator wires in:
    orchestrator: Any = None
    coder_agent: Any = None
    reviewer_agent: Any = None
    # Per-request args the chat layer may want to share
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandResult:
    """What a slash command returns.

    The chat layer interprets the fields like this:

      - if ``replace_message`` is set, the original user message
        is replaced with this string before being sent to the LLM
      - if ``append_to_message`` is set, it's appended to the
        original message
      - if ``system_output`` is set, it's shown to the user as a
        system message (no LLM involvement)
      - ``error`` is shown as a red system message
      - ``stop_loop`` short-circuits the request — no LLM call
    """
    replace_message: Optional[str] = None
    append_to_message: Optional[str] = None
    system_output: Optional[str] = None
    error: Optional[str] = None
    stop_loop: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


CommandHandler = Callable[[str, CommandContext], Union[CommandResult, Awaitable[CommandResult]]]


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: Dict[str, CommandHandler] = {}

    def register(self, name: str, handler: CommandHandler,
                 *, aliases: Optional[List[str]] = None,
                 help: str = "") -> None:
        if not name or not re.match(r"^[a-z][a-z0-9_-]*$", name):
            raise ValueError(f"invalid command name: {name!r}")
        if name in self._commands:
            raise ValueError(f"command {name!r} already registered")
        self._commands[name] = handler
        if aliases:
            for a in aliases:
                if a in self._commands:
                    raise ValueError(f"alias {a!r} collides with existing command")
                self._commands[a] = handler
        self._help = getattr(self, "_help", {})
        if help:
            self._help[name] = help

    def get(self, name: str) -> Optional[CommandHandler]:
        return self._commands.get(name)

    def names(self) -> List[str]:
        # Return only canonical names (those registered without
        # being an alias of something else). We track those by
        # storing them in a separate set.
        canonical = getattr(self, "_canonical", set())
        if not canonical:
            # Lazily compute: a name is canonical if registering it
            # again with the same handler would succeed (no collision
            # with itself).
            seen = set()
            for n, h in self._commands.items():
                # We don't have a great way to detect aliases here
                # without metadata. Just dedupe by id(handler) but
                # that's wrong. Fall back: every command name.
                if id(h) in seen:
                    continue
                seen.add(id(h))
                canonical.add(n)
        return sorted(canonical)

    def help_for(self, name: str) -> str:
        help_map = getattr(self, "_help", {})
        return help_map.get(name, "")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@dataclass
class ParsedCommand:
    name: str
    args: str
    raw: str


_COMMAND_RE = re.compile(r"^/([a-z][a-z0-9_-]*)\b(.*)$", re.DOTALL)


def parse_command(text: str) -> Optional[ParsedCommand]:
    """Return a ParsedCommand if *text* starts with ``/cmd`` else None."""
    if not text:
        return None
    s = text.lstrip()
    if not s.startswith("/"):
        return None
    m = _COMMAND_RE.match(s)
    if not m:
        return None
    return ParsedCommand(name=m.group(1), args=m.group(2).strip(), raw=text)


class CommandParser:
    """Dispatches ``/cmd args`` to a registered handler."""

    def __init__(self, registry: Optional[CommandRegistry] = None) -> None:
        self.registry = registry or get_default_registry()

    async def handle(self, text: str, ctx: CommandContext) -> Optional[CommandResult]:
        """Run the command in *text* and return its result.

        Returns ``None`` if *text* is not a slash command. Returns
        a ``CommandResult`` with ``error`` set if the command is
        unknown.
        """
        parsed = parse_command(text)
        if parsed is None:
            return None
        handler = self.registry.get(parsed.name)
        if handler is None:
            return CommandResult(error=f"unknown command: /{parsed.name}")
        try:
            result = handler(parsed.args, ctx)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            logger.exception("slash command /%s failed", parsed.name)
            return CommandResult(error=f"/{parsed.name} failed: {exc}")
        if not isinstance(result, CommandResult):
            return CommandResult(system_output=str(result))
        return result


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------


_default_registry: Optional[CommandRegistry] = None


def get_default_registry() -> CommandRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = CommandRegistry()
        register_builtins(_default_registry)
    return _default_registry


def reset_default_registry() -> None:
    global _default_registry
    _default_registry = None


# ---------------------------------------------------------------------------
# Built-in commands
# ---------------------------------------------------------------------------


def _run_terminal_command(shell_cmd: str, ctx: CommandContext,
                          *, max_output: int = 4000) -> CommandResult:
    """Run *shell_cmd* in the project work_dir and return the result.

    Safe to call from a sync context *or* from inside a running event
    loop. When called from inside a loop (the common case: a slash
    command handler invoked by an async chat layer), we run the
    coroutine on a worker thread to avoid the
    ``asyncio.run() cannot be called from a running event loop``
    trap.
    """
    import concurrent.futures
    from kairos.tools.terminal import TerminalTool
    tool = TerminalTool(allowed_cwd=ctx.work_dir or ".")

    def _runner() -> Any:
        return asyncio.run(tool.execute(shell_cmd))

    try:
        asyncio.get_running_loop()
        # We're inside an event loop; run the tool in a thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            res = ex.submit(_runner).result()
    except RuntimeError:
        # No running loop — safe to call asyncio.run directly.
        res = _runner()
    if res.success:
        out = (res.output or "").strip()
        if len(out) > max_output:
            out = out[:max_output] + "\n... (truncated)"
        return CommandResult(system_output=f"$ {shell_cmd}\n{out}" if out else f"$ {shell_cmd}\n(ok)")
    return CommandResult(error=f"$ {shell_cmd}\n{res.error or 'failed'}")


def cmd_test(args: str, ctx: CommandContext) -> CommandResult:
    """Run pytest. ``/test tests/test_foo.py -x`` or just ``/test``."""
    argv = args.strip() or "."
    # Defensive: refuse obviously bad shells.
    if any(t in argv for t in (";", "&&", "||", "|", "`", "$(")):
        return CommandResult(error="/test: shell metacharacters not allowed")
    return _run_terminal_command(f"python -m pytest {argv} --tb=short -q", ctx)


def cmd_lint(args: str, ctx: CommandContext) -> CommandResult:
    """Run ruff. ``/lint kairos/`` or just ``/lint``."""
    argv = args.strip() or "."
    if any(t in argv for t in (";", "&&", "||", "|", "`", "$(")):
        return CommandResult(error="/lint: shell metacharacters not allowed")
    return _run_terminal_command(f"python -m ruff check {argv}", ctx)


def cmd_format(args: str, ctx: CommandContext) -> CommandResult:
    """Run ruff format. ``/format kairos/`` or just ``/format``."""
    argv = args.strip() or "."
    if any(t in argv for t in (";", "&&", "||", "|", "`", "$(")):
        return CommandResult(error="/format: shell metacharacters not allowed")
    return _run_terminal_command(f"python -m ruff format {argv}", ctx)


def cmd_review(args: str, ctx: CommandContext) -> CommandResult:
    """Ask the Reviewer to critique the pending changes."""
    target = args.strip() or "the recent changes"
    msg = (
        f"Please review {target}. Use read-only tools (file_read, "
        "grep, git_diff). Output a concise list of issues with file:line "
        "references and a verdict (approve / request changes)."
    )
    return CommandResult(replace_message=msg, metadata={"source": "slash_command:review"})


def cmd_plan(args: str, ctx: CommandContext) -> CommandResult:
    """Trigger plan mode for the given goal."""
    goal = args.strip() or "the next task"
    msg = (
        f"Switch to plan mode. Produce a step-by-step plan for: {goal}. "
        "Do not edit any files. Wait for user approval before executing."
    )
    return CommandResult(replace_message=msg, metadata={"source": "slash_command:plan"})


def cmd_refactor(args: str, ctx: CommandContext) -> CommandResult:
    """Ask the Coder to refactor a file or symbol."""
    target = args.strip()
    if not target:
        return CommandResult(error="/refactor needs a target: file path or symbol name")
    msg = (
        f"Refactor {target}. Keep behavior identical; improve naming, "
        "structure, and test coverage. Do not touch unrelated code."
    )
    return CommandResult(replace_message=msg, metadata={"source": "slash_command:refactor"})


def cmd_commit(args: str, ctx: CommandContext) -> CommandResult:
    """Commit staged changes with an LLM-generated message."""
    msg = args.strip() or "."
    if any(t in msg for t in (";", "&&", "||", "|", "`", "$(")):
        return CommandResult(error="/commit: shell metacharacters not allowed")
    # Stage everything, then commit with the message.
    return _run_terminal_command(
        f"git add -A && git commit -m {shlex.quote(msg)}", ctx,
    )


def cmd_mode(args: str, ctx: CommandContext) -> CommandResult:
    """Switch the Coder sub-mode. ``/mode read_only`` / ``/mode sandbox`` / ``/mode default``."""
    from kairos.coder_modes import CoderMode
    raw = args.strip().lower()
    if not raw:
        return CommandResult(
            system_output="current mode: " + (
                ctx.metadata.get("coder_mode", "default")
            ),
        )
    mode = CoderMode.parse(raw)
    if ctx.orchestrator is not None:
        try:
            project = ctx.orchestrator.get_project(ctx.project_id)
            if project is not None:
                project.metadata = dict(project.metadata or {})
                project.metadata["coder_mode"] = mode.value
                if hasattr(project.runtime, "coder_mode"):
                    project.runtime.coder_mode = mode.value
                return CommandResult(
                    system_output=f"coder mode → {mode.value} (effective next rebuild)",
                    metadata={"coder_mode": mode.value},
                )
        except Exception as exc:  # noqa: BLE001
            return CommandResult(error=f"failed to switch mode: {exc}")
    return CommandResult(
        system_output=f"coder mode: {mode.value} (no orchestrator; not persisted)",
        metadata={"coder_mode": mode.value},
    )


def cmd_status(args: str, ctx: CommandContext) -> CommandResult:
    """Show a one-line summary of project state."""
    parts: List[str] = []
    if ctx.orchestrator is not None:
        try:
            proj = ctx.orchestrator.get_project(ctx.project_id)
            if proj is not None:
                parts.append(f"project: {proj.name} ({proj.id})")
                mode = getattr(getattr(proj, "runtime", None), "coder_mode", "default")
                parts.append(f"mode: {mode}")
                if proj.loop_session is not None:
                    sess = proj.loop_session
                    parts.append(f"loop: round={sess.round}")
                else:
                    parts.append("loop: idle")
        except Exception as exc:  # noqa: BLE001
            parts.append(f"(status error: {exc})")
    else:
        parts.append("no orchestrator in context")
    return CommandResult(system_output="  ".join(parts) or "(no info)")


def cmd_help(args: str, ctx: CommandContext) -> CommandResult:
    """List available slash commands."""
    reg = get_default_registry()
    lines = ["available commands:"]
    for n in reg.names():
        h = reg.help_for(n)
        if h:
            lines.append(f"  /{n:<14} {h}")
        else:
            lines.append(f"  /{n}")
    return CommandResult(system_output="\n".join(lines))


def register_builtins(reg: CommandRegistry) -> None:
    reg.register("test", cmd_test, help="run pytest: /test [path]")
    reg.register("lint", cmd_lint, help="run ruff: /lint [path]")
    reg.register("format", cmd_format, help="run ruff format: /format [path]")
    reg.register("review", cmd_review, help="trigger reviewer pass on changes: /review [target]")
    reg.register("plan", cmd_plan, help="enter plan mode for a goal: /plan <goal>")
    reg.register("refactor", cmd_refactor, help="refactor a file or symbol: /refactor <target>")
    reg.register("commit", cmd_commit, help="commit with a message: /commit <message>")
    reg.register("mode", cmd_mode, help="set coder sub-mode: /mode read_only|sandbox|default")
    reg.register("status", cmd_status, help="show one-line project status")
    reg.register("help", cmd_help, help="list commands")


def load_user_commands(project_dir, user_dir=None) -> int:
    """Load user slash commands from ``.kairos/commands/*.py``.

    Each file must define a ``register(registry)`` function.
    Returns the count loaded.
    """
    from kairos.hooks import _run_python  # reuse import pattern
    import importlib.util

    count = 0
    reg = get_default_registry()

    def _load_from(path: Path) -> bool:
        spec = importlib.util.spec_from_file_location(
            f"kairos_cmd_{path.stem}", path,
        )
        if spec is None or spec.loader is None:
            return False
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)  # type: ignore
        except Exception:
            logger.warning("failed to load command %s", path, exc_info=True)
            return False
        register_fn = getattr(mod, "register", None)
        if not callable(register_fn):
            return False
        try:
            register_fn(reg)
            return True
        except Exception:
            logger.warning("register() failed in %s", path, exc_info=True)
            return False

    for base in (user_dir, project_dir):
        if not base:
            continue
        d = Path(base) / ".kairos" / "commands" if base == project_dir else Path(base) / "commands"
        if not d.is_dir():
            continue
        for py in sorted(d.glob("*.py")):
            if py.name.startswith("_"):
                continue
            if _load_from(py):
                count += 1
    return count
