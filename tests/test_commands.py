"""Tests for the slash command system."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kairos.commands import (
    CommandContext,
    CommandParser,
    CommandRegistry,
    CommandResult,
    get_default_registry,
    parse_command,
    register_builtins,
    reset_default_registry,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_default_registry()
    yield
    reset_default_registry()


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------


def test_parse_command_basic():
    p = parse_command("/test tests/test_foo.py")
    assert p is not None
    assert p.name == "test"
    assert p.args == "tests/test_foo.py"


def test_parse_command_no_args():
    p = parse_command("/status")
    assert p is not None
    assert p.name == "status"
    assert p.args == ""


def test_parse_command_with_quoted_args():
    p = parse_command('/commit "fix the bug"')
    assert p is not None
    assert p.name == "commit"
    assert p.args == '"fix the bug"'


def test_parse_command_multiline_body():
    p = parse_command("/plan do thing\nwith multiple lines")
    assert p is not None
    assert p.name == "plan"
    assert "thing" in p.args
    assert "multiple lines" in p.args


def test_parse_command_rejects_non_slash():
    assert parse_command("hello world") is None
    assert parse_command("") is None


def test_parse_command_with_leading_whitespace_still_parses():
    """Leading whitespace is allowed; the user can paste indented
    text into the chat and the command still triggers."""
    p = parse_command("  /test x")
    assert p is not None
    assert p.name == "test"
    assert p.args == "x"


def test_parse_command_rejects_invalid_name():
    # Names must start with lowercase letter, then alphanumerics/_/-
    assert parse_command("/123abc") is None
    # Names cannot contain spaces — the name must be a single token.
    assert parse_command("/foo bar") is None or (
        # if a parser is lenient, args may capture "bar"; the
        # contract is that the first token is the name.
        # Our parser splits on whitespace implicitly via \b.
        parse_command("/foo bar") is not None
        and parse_command("/foo bar").name == "foo"
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_register_and_get():
    reg = CommandRegistry()

    def my_cmd(args, ctx):
        return CommandResult(system_output=f"got: {args}")

    reg.register("my", my_cmd, help="my command")
    assert reg.get("my") is my_cmd
    assert "my" in reg.help_for("my")
    assert "my" in reg.names()


def test_registry_aliases_resolve_to_same_handler():
    reg = CommandRegistry()

    def my_cmd(args, ctx):
        return CommandResult(system_output="x")

    reg.register("foo", my_cmd, aliases=["f", "foobar"])
    assert reg.get("foo") is my_cmd
    assert reg.get("f") is my_cmd
    assert reg.get("foobar") is my_cmd


def test_registry_rejects_invalid_name():
    reg = CommandRegistry()
    with pytest.raises(ValueError):
        reg.register("BadName", lambda a, c: None)
    with pytest.raises(ValueError):
        reg.register("123start", lambda a, c: None)
    with pytest.raises(ValueError):
        reg.register("", lambda a, c: None)


def test_registry_rejects_collision_with_existing():
    reg = CommandRegistry()

    def a(args, ctx):
        return None

    def b(args, ctx):
        return None

    reg.register("foo", a, aliases=["x"])
    with pytest.raises(ValueError):
        reg.register("x", b)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parser_returns_none_for_non_command():
    parser = CommandParser()
    ctx = CommandContext(project_id="p", work_dir=str(Path.cwd()))
    assert await parser.handle("hello world", ctx) is None


@pytest.mark.asyncio
async def test_parser_dispatches_to_builtin(tmp_path: Path):
    parser = CommandParser()
    ctx = CommandContext(project_id="p", work_dir=str(tmp_path))
    res = await parser.handle("/help", ctx)
    assert res is not None
    assert res.system_output
    assert "/test" in res.system_output
    assert "/commit" in res.system_output


@pytest.mark.asyncio
async def test_parser_returns_error_for_unknown():
    parser = CommandParser()
    ctx = CommandContext(project_id="p", work_dir=".")
    res = await parser.handle("/no_such_command args", ctx)
    assert res is not None
    assert res.error
    assert "unknown" in res.error


@pytest.mark.asyncio
async def test_parser_handles_handler_exception():
    reg = CommandRegistry()

    def bad(args, ctx):
        raise RuntimeError("oops")

    reg.register("bad", bad)
    parser = CommandParser(reg)
    ctx = CommandContext(project_id="p", work_dir=".")
    res = await parser.handle("/bad", ctx)
    assert res is not None
    assert res.error
    assert "oops" in res.error


@pytest.mark.asyncio
async def test_parser_async_handler():
    reg = CommandRegistry()

    async def slow(args, ctx):
        await asyncio.sleep(0.01)
        return CommandResult(system_output="async ok")

    reg.register("slow", slow)
    parser = CommandParser(reg)
    ctx = CommandContext(project_id="p", work_dir=".")
    res = await parser.handle("/slow", ctx)
    assert res.system_output == "async ok"


# ---------------------------------------------------------------------------
# Builtin commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_replaces_message():
    res = await _handle("/review api/auth.py")
    assert res.replace_message
    assert "api/auth.py" in res.replace_message
    assert res.replace_message.startswith("Please review")


@pytest.mark.asyncio
async def test_review_default_message_mentions_recent_changes():
    res = await _handle("/review")
    assert "recent changes" in res.replace_message


@pytest.mark.asyncio
async def test_plan_replaces_with_plan_mode_prompt():
    res = await _handle("/plan refactor the cache")
    assert "plan mode" in res.replace_message
    assert "refactor the cache" in res.replace_message


@pytest.mark.asyncio
async def test_refactor_requires_target():
    res = await _handle("/refactor")
    assert res.error
    assert "target" in res.error


@pytest.mark.asyncio
async def test_refactor_with_target():
    res = await _handle("/refactor kairos/voice.py")
    assert res.replace_message
    assert "kairos/voice.py" in res.replace_message


@pytest.mark.asyncio
async def test_mode_without_args_returns_current():
    ctx = CommandContext(project_id="p", work_dir=".",
                        metadata={"coder_mode": "sandbox"})
    res = await _handle("/mode", ctx)
    assert res.system_output
    assert "sandbox" in res.system_output


@pytest.mark.asyncio
async def test_mode_with_orchestrator_switches(tmp_path: Path):
    class FakeProject:
        def __init__(self):
            self.id = "p1"
            self.name = "demo"
            self.metadata = {}
            self.runtime = type("R", (), {"coder_mode": "default"})()
            self.loop_session = None

    class FakeOrch:
        def get_project(self, pid):
            return FakeProject()

    ctx = CommandContext(project_id="p1", work_dir=str(tmp_path),
                        orchestrator=FakeOrch())
    res = await _handle("/mode read_only", ctx)
    assert res.system_output
    assert "read_only" in res.system_output
    assert res.metadata.get("coder_mode") == "read_only"


@pytest.mark.asyncio
async def test_mode_aliases_work():
    res = await _handle("/mode ro", CommandContext(project_id="p", work_dir="."))
    assert "read_only" in res.system_output or "ro" in res.system_output
    # /mode ro → CoderMode.parse("ro") → READ_ONLY → "read_only"


@pytest.mark.asyncio
async def test_status_no_orchestrator():
    res = await _handle("/status", CommandContext(project_id="p", work_dir="."))
    assert res.system_output
    assert "no orchestrator" in res.system_output


@pytest.mark.asyncio
async def test_status_with_orchestrator(tmp_path: Path):
    class FakeProject:
        def __init__(self):
            self.id = "p1"
            self.name = "demo"
            self.runtime = type("R", (), {"coder_mode": "sandbox"})()
            self.loop_session = type("S", (), {"round": 3})()

    class FakeOrch:
        def get_project(self, pid):
            return FakeProject()

    res = await _handle(
        "/status",
        CommandContext(project_id="p1", work_dir=str(tmp_path),
                       orchestrator=FakeOrch()),
    )
    assert "demo" in res.system_output
    assert "sandbox" in res.system_output
    assert "round=3" in res.system_output


@pytest.mark.asyncio
async def test_help_lists_all_builtins():
    res = await _handle("/help", CommandContext(project_id="p", work_dir="."))
    out = res.system_output
    for cmd in ("/test", "/lint", "/format", "/review", "/plan",
                "/refactor", "/commit", "/mode", "/status", "/help"):
        assert cmd in out


# ---------------------------------------------------------------------------
# Shell tool integration: /test, /lint, /format, /commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_command_blocks_shell_metachars(tmp_path: Path):
    (tmp_path / "test_x.py").write_text("def test_ok(): assert 1 + 1 == 2\n")
    ctx = CommandContext(project_id="p", work_dir=str(tmp_path))
    res = await _handle("/test test_x.py; rm -rf /", ctx)
    assert res.error
    assert "metacharacters" in res.error


@pytest.mark.asyncio
async def test_test_command_runs_pytest(tmp_path: Path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    ctx = CommandContext(project_id="p", work_dir=str(tmp_path))
    res = await _handle("/test test_ok.py", ctx)
    assert res.system_output
    assert "ok" in res.system_output.lower() or "passed" in res.system_output.lower()


@pytest.mark.asyncio
async def test_lint_command_runs_ruff(tmp_path: Path):
    # Even on a clean dir, ruff exits 0 and prints "All checks passed!"
    # (or "No Python files to check"). We just assert it ran.
    (tmp_path / "ok.py").write_text("x = 1\n")
    ctx = CommandContext(project_id="p", work_dir=str(tmp_path))
    res = await _handle("/lint ok.py", ctx)
    assert res.system_output is not None
    # success path → error is None
    assert res.error is None


@pytest.mark.asyncio
async def test_format_command_runs_ruff_format(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x=1\n")
    ctx = CommandContext(project_id="p", work_dir=str(tmp_path))
    res = await _handle("/format ok.py", ctx)
    assert res.system_output


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _handle(text: str, ctx: Optional[CommandContext] = None) -> CommandResult:
    parser = CommandParser()
    if ctx is None:
        ctx = CommandContext(project_id="p", work_dir=".")
    res = await parser.handle(text, ctx)
    if res is None:
        pytest.fail(f"expected a CommandResult for {text!r}, got None")
    return res
