"""Tests for the kairos.hooks system."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from kairos.hooks import (
    HookContext,
    HookDecision,
    HookEvent,
    HookRegistry,
    HookResult,
    HookSpec,
    get_default_registry,
    load_project_hooks,
    reset_default_registry,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_default_registry()
    yield
    reset_default_registry()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_command_hook():
    reg = HookRegistry()
    spec = HookSpec(event=HookEvent.PRE_TOOL_USE, matcher="terminal",
                    hook_type="command", command="echo hi")
    reg.register(spec)
    assert reg.hooks_for(HookEvent.PRE_TOOL_USE, "terminal") == [spec]
    assert reg.hooks_for(HookEvent.PRE_TOOL_USE, "file_read") == []


def test_register_builtin_hook():
    reg = HookRegistry()

    def my_hook(ctx: HookContext) -> HookResult:
        return HookResult(decision=HookDecision.ALLOW, reason="ok")

    reg.register_builtin(HookEvent.STOP, my_hook)
    assert len(reg.hooks_for(HookEvent.STOP)) == 1


def test_matcher_filters_tools():
    reg = HookRegistry()
    reg.register(HookSpec(event=HookEvent.PRE_TOOL_USE, matcher=r"^file_",
                          hook_type="command", command="true"))
    assert reg.hooks_for(HookEvent.PRE_TOOL_USE, "file_read")
    assert reg.hooks_for(HookEvent.PRE_TOOL_USE, "file_edit")
    assert not reg.hooks_for(HookEvent.PRE_TOOL_USE, "terminal")


def test_matcher_none_matches_all():
    reg = HookRegistry()
    reg.register(HookSpec(event=HookEvent.STOP, matcher=None,
                          hook_type="command", command="true"))
    assert reg.hooks_for(HookEvent.STOP, "anything")
    assert reg.hooks_for(HookEvent.STOP, "x")


def test_clear_resets_registry():
    reg = HookRegistry()
    reg.register(HookSpec(event=HookEvent.STOP, matcher=None,
                          hook_type="command", command="true"))
    assert reg.hooks_for(HookEvent.STOP)
    reg.clear()
    assert not reg.hooks_for(HookEvent.STOP)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def test_load_yaml_pre_and_post(tmp_path: Path):
    yaml = """
hooks:
  PreToolUse:
    - matcher: "terminal"
      type: command
      command: "echo about-to"
      on_error: deny
  PostToolUse:
    - matcher: "file_edit"
      type: command
      command: "true"
"""
    p = tmp_path / "hooks.yaml"
    p.write_text(yaml, encoding="utf-8")
    reg = HookRegistry()
    n = reg.load_yaml(p)
    assert n == 2
    assert len(reg.hooks_for(HookEvent.PRE_TOOL_USE, "terminal")) == 1
    assert len(reg.hooks_for(HookEvent.POST_TOOL_USE, "file_edit")) == 1


def test_load_yaml_invalid_returns_zero(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("hooks: [[[", encoding="utf-8")  # invalid YAML
    reg = HookRegistry()
    assert reg.load_yaml(p) == 0


def test_load_yaml_unknown_event_skipped(tmp_path: Path):
    yaml = """
hooks:
  NotAnEvent:
    - type: command
      command: "true"
  PreToolUse:
    - type: command
      command: "true"
"""
    p = tmp_path / "hooks.yaml"
    p.write_text(yaml, encoding="utf-8")
    reg = HookRegistry()
    n = reg.load_yaml(p)
    assert n == 1  # only the valid one


def test_load_yaml_bad_spec_skipped(tmp_path: Path):
    yaml = """
hooks:
  PreToolUse:
    - type: bogus-type
      command: "true"
    - type: command
      command: "echo ok"
"""
    p = tmp_path / "hooks.yaml"
    p.write_text(yaml, encoding="utf-8")
    reg = HookRegistry()
    n = reg.load_yaml(p)
    assert n == 1  # only the valid one


def test_load_yaml_missing_file(tmp_path: Path):
    reg = HookRegistry()
    assert reg.load_yaml(tmp_path / "nonexistent.yaml") == 0


# ---------------------------------------------------------------------------
# Command hook execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_hook_exit_0_allows():
    reg = HookRegistry()
    reg.register(HookSpec(event=HookEvent.PRE_TOOL_USE, matcher="terminal",
                          hook_type="command", command="python -c \"pass\""))
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE,
                      project_id="p1", tool_name="terminal",
                      tool_input={"command": "ls"})
    result = await reg.run(ctx)
    assert result.decision == HookDecision.ALLOW


@pytest.mark.asyncio
async def test_command_hook_exit_nonzero_denies():
    reg = HookRegistry()
    reg.register(HookSpec(event=HookEvent.PRE_TOOL_USE, matcher="terminal",
                          hook_type="command", command="python -c \"import sys; sys.exit(3)\""))
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE,
                      project_id="p1", tool_name="terminal",
                      tool_input={"command": "ls"})
    result = await reg.run(ctx)
    assert result.decision == HookDecision.DENY
    assert "exit=" in result.reason


@pytest.mark.asyncio
async def test_command_hook_receives_env():
    """$TOOL_NAME, $TOOL_INPUT, $KAIROS_PROJECT_ID are set."""
    reg = HookRegistry()
    import tempfile
    fd, out_path_str = tempfile.mkstemp(prefix="hook-", suffix=".txt")
    os.close(fd)
    out_path = Path(out_path_str)
    # Use a here-doc / script file so we don't fight shell escaping.
    script = tmp_path = out_path.parent / "_hook_writer.py"
    script.write_text(
        "import os, pathlib\n"
        f"pathlib.Path({str(out_path)!r}).write_text(\n"
        "    os.environ.get('TOOL_NAME', '') + '|' +\n"
        "    os.environ.get('KAIROS_PROJECT_ID', '') + '|' +\n"
        "    os.environ.get('TOOL_INPUT', '')\n"
        ")\n",
        encoding="utf-8",
    )
    try:
        # On Windows cmd.exe, single quotes around the path get stripped
        # and break the call. Use a path without spaces and no quotes.
        script_path = str(script)
        assert " " not in script_path, f"test path has spaces, can't bypass quoting: {script_path}"
        reg.register(HookSpec(event=HookEvent.PRE_TOOL_USE, matcher="terminal",
                              hook_type="command",
                              command=f"python {script_path}"))
        ctx = HookContext(event=HookEvent.PRE_TOOL_USE,
                          project_id="my-proj", tool_name="terminal",
                          tool_input={"command": "ls"})
        result = await reg.run(ctx)
        body = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if not body:
            assert False, f"hook output: {result.message!r}"
        assert "terminal" in body, body
        assert "my-proj" in body
        assert "ls" in body
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass
        try:
            script.unlink()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_command_hook_timeout_does_not_hang():
    reg = HookRegistry()
    reg.register(HookSpec(event=HookEvent.PRE_TOOL_USE, matcher="x",
                          hook_type="command",
                          command="python -c \"import time; time.sleep(5)\"",
                          timeout_s=0.5, on_error="allow"))
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE,
                      project_id="p", tool_name="x")
    loop = asyncio.get_event_loop()
    start = loop.time()
    result = await reg.run(ctx)
    elapsed = loop.time() - start
    assert elapsed < 4.0, f"timeout should have fired fast, took {elapsed:.1f}s"
    # on_error=allow means the failure is logged and we proceed.
    assert result.decision == HookDecision.ALLOW


# ---------------------------------------------------------------------------
# _force_kill: a hook that times out must not take the run down with it
# ---------------------------------------------------------------------------


def _fake_posix_os(monkeypatch, *, getpgid, killpg):
    """Give the hooks module a POSIX-looking ``os`` without touching the real one.

    Patching ``os.name`` globally makes pathlib build PosixPath on Windows and
    pytest falls over while it is only trying to format a failure — so swap the
    name inside the module's namespace instead, keeping every other attribute real
    (the module also uses os.environ and os.path).

    ``signal.SIGKILL`` does not exist on Windows, and the POSIX branch needs it, so
    it is added here too; without it the branch dies of an AttributeError that the
    surrounding ``except Exception`` swallows silently.
    """
    import signal
    import types

    import kairos.hooks as hooks_mod

    if not hasattr(signal, "SIGKILL"):
        monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)

    fake = types.ModuleType("os")
    fake.__dict__.update(vars(os))
    fake.name = "posix"
    fake.getpgid = getpgid
    fake.killpg = killpg
    monkeypatch.setattr(hooks_mod, "os", fake, raising=True)
    return fake


@pytest.mark.asyncio
async def test_force_kill_never_targets_its_own_process_group(monkeypatch):
    """A hook sharing our process group must be killed directly, never by group.

    ``os.killpg(os.getpgid(child))`` SIGKILLs the whole group. When the hook's
    subprocess is not started in its own session that group is *ours*, so a hook
    timing out killed the test run itself: on Linux the CI shard holding this file
    died with exit 137 and no traceback, on every run, while the suite stayed green
    on Windows — which uses taskkill and has no process groups at all.
    """
    calls: dict = {}

    class FakeProc:
        pid = 4242

        def kill(self):
            calls["kill"] = True

        async def wait(self):
            return 0

    _fake_posix_os(monkeypatch,
                   getpgid=lambda pid: 7777,  # same group for us and the child
                   killpg=lambda pgid, sig: calls.__setitem__("killpg", pgid))

    await HookRegistry._force_kill(FakeProc())

    assert calls.get("kill") is True, "the child process itself must be killed"
    assert "killpg" not in calls, "must never kill the group this process belongs to"


@pytest.mark.asyncio
async def test_force_kill_uses_killpg_when_the_group_is_its_own(monkeypatch):
    """With its own session the hook's group can be killed as a tree."""
    calls: dict = {}

    class FakeProc:
        pid = 4242

        async def wait(self):
            return 0

    _fake_posix_os(monkeypatch,
                   getpgid=lambda pid: 7777 if pid == 4242 else 5555,
                   killpg=lambda pgid, sig: calls.__setitem__("killpg", pgid))

    await HookRegistry._force_kill(FakeProc())

    assert calls.get("killpg") == 7777


@pytest.mark.asyncio
async def test_command_hook_gets_its_own_session(monkeypatch):
    """The creation site must ask for a new session, or that kill is a suicide."""
    seen: dict = {}

    async def fake_create(*args, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("inspecting the call is enough")

    _fake_posix_os(monkeypatch, getpgid=lambda pid: 1, killpg=lambda pgid, sig: None)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create)

    reg = HookRegistry()
    spec = HookSpec(event=HookEvent.PRE_TOOL_USE, matcher="x",
                    hook_type="command", command="true")
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, project_id="p", tool_name="x")
    await reg._run_command(spec, ctx)  # swallows the RuntimeError above

    assert seen.get("start_new_session") is True


# ---------------------------------------------------------------------------
# Builtin hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builtin_hook_can_deny():
    reg = HookRegistry()

    def deny_hook(ctx: HookContext) -> HookResult:
        if ctx.tool_input.get("command") == "rm":
            return HookResult(decision=HookDecision.DENY, reason="no rm")
        return HookResult(decision=HookDecision.ALLOW)

    reg.register_builtin(HookEvent.PRE_TOOL_USE, deny_hook)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE,
                      project_id="p", tool_name="terminal",
                      tool_input={"command": "rm"})
    result = await reg.run(ctx)
    assert result.decision == HookDecision.DENY
    assert "no rm" in result.reason


@pytest.mark.asyncio
async def test_builtin_async_hook_works():
    reg = HookRegistry()

    async def async_hook(ctx: HookContext) -> HookResult:
        await asyncio.sleep(0.01)
        return HookResult(decision=HookDecision.ALLOW, message="async ok")

    reg.register_builtin(HookEvent.POST_TOOL_USE, async_hook)
    ctx = HookContext(event=HookEvent.POST_TOOL_USE,
                      project_id="p", tool_name="x",
                      tool_output="ok")
    result = await reg.run(ctx)
    assert result.decision == HookDecision.ALLOW
    assert "async ok" in result.message


@pytest.mark.asyncio
async def test_builtin_hook_exception_caught():
    reg = HookRegistry()

    def boom(ctx: HookContext) -> HookResult:
        raise RuntimeError("oops")

    reg.register_builtin(HookEvent.STOP, boom, on_error="allow")
    ctx = HookContext(event=HookEvent.STOP, project_id="p", tool_name="x")
    result = await reg.run(ctx)
    # on_error=allow means the failure is silent and we proceed.
    assert result.decision == HookDecision.ALLOW


@pytest.mark.asyncio
async def test_builtin_hook_exception_with_on_error_deny():
    reg = HookRegistry()

    def boom(ctx: HookContext) -> HookResult:
        raise RuntimeError("oops")

    reg.register_builtin(HookEvent.PRE_TOOL_USE, boom, on_error="deny")
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, project_id="p", tool_name="x")
    result = await reg.run(ctx)
    assert result.decision == HookDecision.DENY


# ---------------------------------------------------------------------------
# First-DENY-wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_deny_wins():
    reg = HookRegistry()
    reg.register(HookSpec(event=HookEvent.PRE_TOOL_USE, matcher="terminal",
                          hook_type="command", command="python -c \"import sys; sys.exit(1)\""))  # DENY
    reg.register_builtin(
        HookEvent.PRE_TOOL_USE,
        lambda c: HookResult(message="this never runs"),
    )
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, project_id="p",
                      tool_name="terminal")
    result = await reg.run(ctx)
    assert result.decision == HookDecision.DENY


# ---------------------------------------------------------------------------
# Python module hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_python_module_hook_calls_function(monkeypatch, tmp_path: Path):
    """A python hook imports a module and calls a named function."""
    # Create a real module file.
    mod_dir = tmp_path / "myhooks"
    mod_dir.mkdir()
    (mod_dir / "__init__.py").write_text("", encoding="utf-8")
    (mod_dir / "h.py").write_text(
        "from kairos.hooks import HookResult, HookDecision\n"
        "def my_hook(ctx):\n"
        "    return HookResult(decision=HookDecision.ALLOW,\n"
        "                       message=f'from-mod: {ctx.tool_name}')\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        reg = HookRegistry()
        reg.register(HookSpec(event=HookEvent.POST_TOOL_USE,
                              matcher=None,
                              hook_type="python",
                              module="myhooks.h",
                              function="my_hook"))
        ctx = HookContext(event=HookEvent.POST_TOOL_USE,
                          project_id="p", tool_name="x",
                          tool_output="ok")
        result = await reg.run(ctx)
        assert "from-mod: x" in result.message
    finally:
        sys.path.remove(str(tmp_path))


@pytest.mark.asyncio
async def test_python_module_hook_missing_function_returns_allow():
    reg = HookRegistry()
    reg.register(HookSpec(event=HookEvent.STOP, matcher=None,
                          hook_type="python",
                          module="nonexistent_module_xyz",
                          function="missing"))
    ctx = HookContext(event=HookEvent.STOP, project_id="p", tool_name="x")
    # Hook errored silently → on_error default is "allow".
    result = await reg.run(ctx)
    assert result.decision == HookDecision.ALLOW


# ---------------------------------------------------------------------------
# Project/user hook loading
# ---------------------------------------------------------------------------


def test_load_project_hooks(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    (proj / ".kairos").mkdir()
    (proj / ".kairos" / "hooks.yaml").write_text(
        "hooks:\n  Stop:\n    - type: command\n      command: 'echo proj'\n",
        encoding="utf-8",
    )
    (user / "hooks.yaml").write_text(
        "hooks:\n  Stop:\n    - type: command\n      command: 'echo user'\n",
        encoding="utf-8",
    )
    n = load_project_hooks(proj, user)
    assert n == 2
    assert len(get_default_registry().hooks_for(HookEvent.STOP)) == 2


# ---------------------------------------------------------------------------
# Round 5: SessionStart / SessionEnd
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_event_fires():
    reg = HookRegistry()
    fired = []

    def hook(ctx: HookContext) -> HookResult:
        fired.append((ctx.event.value, ctx.metadata.get("session_id")))
        return HookResult()

    reg.register_builtin(HookEvent.SESSION_START, hook)
    ctx = HookContext(
        event=HookEvent.SESSION_START,
        project_id="p1",
        metadata={"session_id": "s-123", "requirement": "build a thing"},
    )
    res = await reg.run(ctx)
    assert res.decision == HookDecision.ALLOW
    assert fired == [("SessionStart", "s-123")]


@pytest.mark.asyncio
async def test_session_end_event_fires():
    reg = HookRegistry()
    fired = []

    def hook(ctx: HookContext) -> HookResult:
        fired.append((ctx.event.value, ctx.metadata.get("outcome")))
        return HookResult()

    reg.register_builtin(HookEvent.SESSION_END, hook)
    ctx = HookContext(
        event=HookEvent.SESSION_END,
        project_id="p1",
        metadata={"session_id": "s-456", "outcome": "approved"},
    )
    await reg.run(ctx)
    assert fired == [("SessionEnd", "approved")]


@pytest.mark.asyncio
async def test_session_start_command_hook_runs():
    """SessionStart command hooks receive the right env vars."""
    import tempfile
    fd, out_path_str = tempfile.mkstemp(prefix="hook-", suffix=".txt")
    os.close(fd)
    out_path = Path(out_path_str)
    script = out_path.parent / "_session_start_writer.py"
    script.write_text(
        "import os, pathlib\n"
        f"pathlib.Path({str(out_path)!r}).write_text(\n"
        "    os.environ.get('KAIROS_EVENT', '') + '|' +\n"
        "    os.environ.get('KAIROS_PROJECT_ID', '') + '|' +\n"
        "    os.environ.get('SESSION_ID', '__no_session__')\n"
        ")\n",
        encoding="utf-8",
    )
    try:
        reg = HookRegistry()
        reg.register(HookSpec(
            event=HookEvent.SESSION_START, matcher=None,
            hook_type="command", command=f"python {str(script)}",
        ))
        await reg.run(HookContext(
            event=HookEvent.SESSION_START,
            project_id="my-proj",
            metadata={"session_id": "s-abc"},
        ))
        body = out_path.read_text(encoding="utf-8")
        # KAIROS_EVENT and KAIROS_PROJECT_ID come from _env_for
        # The SESSION_ID one is in our test script only — it may
        # not be set. Just check the two known ones.
        assert "SessionStart" in body
        assert "my-proj" in body
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass
        try:
            script.unlink()
        except OSError:
            pass


def test_hook_event_enum_has_all_five():
    """The HookEvent enum should have all 5 events the project
    needs (PreToolUse, PostToolUse, SessionStart, SessionEnd,
    Stop)."""
    names = {e.value for e in HookEvent}
    assert names == {"PreToolUse", "PostToolUse", "SessionStart",
                     "SessionEnd", "Stop"}


def test_session_start_hooks_for_empty_when_no_registration():
    """When no SessionStart hook is registered, hooks_for returns []. """
    reset_default_registry()
    reg = get_default_registry()
    assert reg.hooks_for(HookEvent.SESSION_START) == []
    assert reg.hooks_for(HookEvent.SESSION_END) == []


@pytest.mark.asyncio
async def test_session_start_yaml_loading():
    """Round 5: YAML can register SessionStart / SessionEnd hooks."""
    yaml = """
hooks:
  SessionStart:
    - type: command
      command: "echo starting"
  SessionEnd:
    - matcher: "ignored"
      type: command
      command: "echo ending"
"""
    import tempfile
    fd, path_str = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    p = Path(path_str)
    p.write_text(yaml, encoding="utf-8")
    try:
        reg = HookRegistry()
        n = reg.load_yaml(p)
        assert n == 2
        assert len(reg.hooks_for(HookEvent.SESSION_START)) == 1
        assert len(reg.hooks_for(HookEvent.SESSION_END)) == 1
    finally:
        p.unlink(missing_ok=True)
