"""The agent must never pop a console window when it runs a command.

Kairos ships as a *windowed* EXE (PyInstaller ``--windowed``, no console). On
Windows, a console-less process that ``CreateProcess()``-es a **console**
program -- ``cmd.exe``, git-bash, ``git.exe``, ``taskkill.exe`` -- makes the
kernel allocate a fresh console window, so every command the agent ran flashed
a black box on screen. ``CREATE_NO_WINDOW`` (``0x08000000``) suppresses that;
``kairos.platform_flags.hidden_kwargs()`` is the one place that flag is applied.

This module pins the fix two ways:

* **Behaviour** -- the representative spawn sites (terminal in full-access and
  argv mode, the MCP stdio server, command hooks) are driven with the low-level
  spawn monkeypatched, and the captured ``creationflags`` is asserted.
* **Source guard** -- every spawn call in the shipped code is re-scanned, and a
  call that carries neither the helper nor an explicit ``creationflags`` fails
  the build. That is what stops the *next* spawn site from silently
  reintroducing the flicker.
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path

import pytest

from kairos.platform_flags import CREATE_NO_WINDOW, hidden_kwargs, is_windows

WINDOWS = sys.platform.startswith("win")

REPO = Path(__file__).resolve().parents[1]


def _assert_hidden(kwargs: dict) -> None:
    """The captured subprocess kwargs must hide the console (on Windows)."""
    if WINDOWS:
        flags = int(kwargs.get("creationflags", 0) or 0)
        assert flags & CREATE_NO_WINDOW, (
            f"child would pop a console window: creationflags={flags:#x}"
        )
    else:
        # Cross-platform promise: nothing is injected off Windows.
        assert "creationflags" not in kwargs


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def test_windows_injects_create_no_window(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert is_windows() is True
    assert hidden_kwargs() == {"creationflags": CREATE_NO_WINDOW}


def test_non_windows_returns_empty(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert is_windows() is False
    assert hidden_kwargs() == {}
    # An existing kwargs dict is passed through untouched off Windows.
    assert hidden_kwargs({"start_new_session": True}) == {"start_new_session": True}


def test_merge_ors_creationflags_and_does_not_mutate(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    original = {"creationflags": 0x00000200, "stdout": "PIPE"}
    merged = hidden_kwargs(original)
    # OR-ed, not overwritten: the caller keeps its own process-group flag.
    assert merged["creationflags"] == 0x00000200 | CREATE_NO_WINDOW
    assert merged["stdout"] == "PIPE"
    assert original == {"creationflags": 0x00000200, "stdout": "PIPE"}  # untouched


# ---------------------------------------------------------------------------
# Behaviour: the representative call sites really pass the flag
# ---------------------------------------------------------------------------


class _Recorder:
    """Replaces asyncio.create_subprocess_* and records the kwargs.

    Raising aborts the spawn before a real process exists; every caller here
    swallows the exception into a failed result, which is exactly the path we
    only want to reach far enough to inspect the kwargs.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def install(self, monkeypatch) -> None:
        async def fake(*args, **kwargs):
            self.calls.append(kwargs)
            # OSError is swallowed by every caller (terminal -> failed result,
            # hooks -> no-op, MCP -> McpError), so the spawn never happens.
            raise OSError("inspecting the spawn call is enough")

        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)


@pytest.fixture(autouse=True)
def _no_ambient_full_access(monkeypatch):
    monkeypatch.delenv("KAIROS_FULL_ACCESS", raising=False)
    yield


@pytest.mark.asyncio
async def test_terminal_full_access_hides_console(monkeypatch, tmp_path):
    from kairos.tools.terminal import TerminalTool

    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "1")

    term = TerminalTool(allowed_cwd=str(tmp_path))
    await term.execute("echo full-access")   # reaches the spawn, then aborts

    assert rec.calls, "terminal never reached a spawn call"
    for kwargs in rec.calls:
        _assert_hidden(kwargs)


@pytest.mark.asyncio
async def test_terminal_sandboxed_argv_hides_console(monkeypatch, tmp_path):
    from kairos.tools.terminal import TerminalTool

    rec = _Recorder()
    rec.install(monkeypatch)

    term = TerminalTool(allowed_cwd=str(tmp_path))
    # A single, allow-listed command: runs argv-style (no shell), so this is
    # the create_subprocess_exec branch.
    await term.execute("dir" if os.name == "nt" else "ls")

    assert rec.calls, "sandboxed terminal never reached a spawn call"
    for kwargs in rec.calls:
        _assert_hidden(kwargs)


def test_mcp_stdio_spawn_hides_console(monkeypatch):
    from kairos import mcp_client as mc

    rec = _Recorder()
    rec.install(monkeypatch)

    client = mc.StdioMcpClient(mc.McpServerConfig(name="probe", command="probe-bin"))
    with pytest.raises(mc.McpError):
        asyncio.run(client.start())

    assert rec.calls, "MCP client never reached a spawn call"
    kwargs = rec.calls[0]
    _assert_hidden(kwargs)
    # The fix must NOT have touched the pipes the protocol rides on.
    assert kwargs["stdin"] is asyncio.subprocess.PIPE
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_command_hook_hides_console(monkeypatch):
    from kairos.hooks import HookContext, HookEvent, HookRegistry, HookSpec

    rec = _Recorder()
    rec.install(monkeypatch)

    reg = HookRegistry()
    spec = HookSpec(event=HookEvent.PRE_TOOL_USE, matcher="x",
                    hook_type="command", command="true")
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, project_id="p", tool_name="x")
    await reg._run_command(spec, ctx)   # swallows the abort

    assert rec.calls, "hook never reached a spawn call"
    for kwargs in rec.calls:
        _assert_hidden(kwargs)


# ---------------------------------------------------------------------------
# Source guard: no spawn site may forget the flag
# ---------------------------------------------------------------------------

#: Directories whose spawn calls are exempt. Every entry carries the reason it
#: is safe: these are NOT the shipped windowed app, so a console flash there is
#: either expected or invisible.
DIR_WHITELIST = {
    "tests": "test fixtures (git init, taskkill of test children) run under a "
             "console-attached pytest, never inside the windowed EXE",
    "scripts": "developer/CI tooling invoked from a terminal by hand",
    "docs": "documentation snippets and a sample pre-commit hook",
    "kairos/skills": "SKILL.md authoring assets, not executed by the app",
    "vendor": "third-party wheels, not our code",
    "web": "frontend build output",
    "dist": "packaged build artifacts",
    "build": "packaged build artifacts",
    "node_modules": "third-party JS deps",
    "examples": "standalone samples run from a shell",
    "__pycache__": "compiled bytecode",
    ".venv": "the virtualenv itself",
    ".git": "repository internals",
    ".pytest_cache": "pytest cache",
    ".ruff_cache": "ruff cache",
    ".kairos": "runtime data dir",
    "logs": "runtime logs",
    "runs": "runtime output",
    "123": "scratch dir",
    "AgnesCode": "vendored scratch project",
}

#: Individual files exempt by absolute-ish repo-relative path (+ reason).
FILE_WHITELIST: dict[str, str] = {}

_SPAWN_NAMES = {"create_subprocess_shell", "create_subprocess_exec"}
_SPAWN_METHODS = {"Popen", "run", "check_output", "call", "check_call"}
_OS_METHODS = {"system", "popen"}


def _root_name(node: ast.AST):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_spawn(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return isinstance(func, ast.Name) and func.id in _SPAWN_NAMES
    root = _root_name(func)
    if func.attr in _SPAWN_NAMES:
        return True                      # asyncio.create_subprocess_*
    if func.attr in _SPAWN_METHODS and root and "subprocess" in root.lower():
        return True                      # subprocess.run / Popen / _subprocess.run
    if func.attr in _OS_METHODS and root == "os":
        return True                      # os.system / os.popen
    return False


def _iter_python_files(root: Path):
    """Yield .py files under *root*, pruning whitelisted dirs during the walk.

    A plain ``rglob`` descends into ``.venv``/``dist`` before the per-file
    check filters them out, which turned this guard into a 20-second scan.
    """
    prune_top = {p for p in DIR_WHITELIST if "/" not in p}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [
            d for d in dirnames
            if not (rel_dir == Path(".") and d in prune_top)
            and (rel_dir / d).as_posix() not in DIR_WHITELIST
        ]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def test_every_spawn_site_hides_the_console():
    """Scan the whole repo; any spawn without the flag (or an explicit
    creationflags) fails, unless its directory is whitelisted above."""
    whitelisted_dirs = {Path(p) for p in DIR_WHITELIST}
    violations: list[str] = []
    scanned = 0

    for path in sorted(_iter_python_files(REPO)):
        rel = path.relative_to(REPO)
        if any(rel == d or d in rel.parents for d in whitelisted_dirs):
            continue
        if rel.as_posix() in FILE_WHITELIST:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not _is_spawn(node):
                continue
            scanned += 1
            segment = ast.get_source_segment(source, node) or ""
            if "hidden_kwargs" in segment or "creationflags" in segment:
                continue
            violations.append(f"{rel.as_posix()}:{node.lineno} "
                              f"{source.splitlines()[node.lineno - 1].strip()}")

    assert not violations, (
        "spawn call(s) would pop a console window in the windowed build -- "
        "add **hidden_kwargs() (kairos.platform_flags), merge it if the call "
        "already sets creationflags, or whitelist the directory with a reason:\n"
        + "\n".join(violations)
    )
    # A parser that silently matches nothing would make the guard useless.
    assert scanned >= 25, f"guard matched only {scanned} spawn calls; parser broke?"
