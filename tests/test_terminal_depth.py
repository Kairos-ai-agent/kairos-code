"""Tests for the upgraded Bash tool (env / timeout / stdin / streaming)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from kairos.tools.terminal import TerminalTool


# ---------------------------------------------------------------------------
# Customized allow-list so we can run more commands in tests
# ---------------------------------------------------------------------------


class _RichTerminal(TerminalTool):
    """A Terminal with the test-friendly command set enabled.

    The default allow-list is conservative (no ``bash``, no
    ``powershell``). Tests need those to verify cross-platform
    semantics, so we extend it.
    """
    ALLOWED_COMMANDS = {
        **TerminalTool.ALLOWED_COMMANDS,
        "bash": (),
        "sh": (),
        "powershell": (),
        "cmd": (),
        "sleep": (),
        "python": (),
        "python3": (),
    }
    # Don't deny stdout-streamed test patterns.
    DENY_PATTERNS = [p for p in TerminalTool.DENY_PATTERNS
                     if "bash" not in p and "sh" not in p and "powershell" not in p and "cmd" not in p]


@pytest.fixture
def term(tmp_path: Path) -> _RichTerminal:
    return _RichTerminal(allowed_cwd=str(tmp_path))


def _is_posix() -> bool:
    return os.name != "nt"


# ---------------------------------------------------------------------------
# env overrides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_override_passes_through(term, tmp_path: Path):
    # Cross-platform: use python to read env vars.
    (tmp_path / "print.py").write_text(
        "import os; print('FOO=' + os.environ.get('FOO', '')); "
        "print('BAR=' + os.environ.get('BAR', ''))",
        encoding="utf-8",
    )
    res = await term.execute(
        "python print.py",
        cwd=str(tmp_path),
        env={"FOO": "hello", "BAR": "world"},
    )
    assert res.success, res.error
    assert "FOO=hello" in res.output
    assert "BAR=world" in res.output


@pytest.mark.asyncio
async def test_env_override_can_unset(term, tmp_path: Path):
    # Cross-platform: use python to verify env was unset.
    (tmp_path / "check.py").write_text(
        "import os; print('UNSET_VAR=' + os.environ.get('UNSET_VAR', '<<absent>>'))",
        encoding="utf-8",
    )
    res = await term.execute(
        "python check.py",
        cwd=str(tmp_path),
        env={"UNSET_VAR": None},
    )
    assert res.success
    assert "UNSET_VAR=<<absent>>" in res.output


@pytest.mark.asyncio
async def test_env_overrides_recorded_in_metadata(term):
    res = await term.execute(
        "echo ok",
        env={"MY_FLAG": "1", "OTHER": "x"},
    )
    assert res.success
    assert "MY_FLAG" in res.metadata["env_overrides"]
    assert "OTHER" in res.metadata["env_overrides"]


# ---------------------------------------------------------------------------
# timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_timeout_shorter_than_default(term):
    # Cross-platform: use python sleep.
    start = time.perf_counter()
    res = await term.execute("python -c \"import time; time.sleep(5)\"", timeout_s=0.5)
    elapsed = time.perf_counter() - start
    assert not res.success
    assert res.metadata["timed_out"] is True
    assert "timed out" in (res.error or "")
    assert elapsed < 3.0, f"timeout should have fired fast, took {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_default_timeout_backwards_compatible(term):
    """Without timeout_s, default 60s applies — short command still works."""
    res = await term.execute("echo hi")
    assert res.success
    assert "hi" in res.output
    assert "duration_s" in res.metadata


@pytest.mark.asyncio
async def test_long_timeout_for_slow_command(term, tmp_path: Path):
    # Cross-platform: use python sleep.
    (tmp_path / "slow.py").write_text(
        "import time; time.sleep(0.3); print('done')",
        encoding="utf-8",
    )
    res = await term.execute("python slow.py", cwd=str(tmp_path), timeout_s=5.0)
    assert res.success
    assert "done" in res.output
    assert res.metadata["duration_s"] >= 0.3


# ---------------------------------------------------------------------------
# stdin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdin_piped_to_child(term, tmp_path: Path):
    # Cross-platform: use python read.
    (tmp_path / "stdin_reader.py").write_text(
        "import sys; line = sys.stdin.read().strip(); print('got: ' + line)",
        encoding="utf-8",
    )
    res = await term.execute(
        "python stdin_reader.py",
        cwd=str(tmp_path),
        stdin="hello-from-stdin\n",
    )
    assert res.success, res.error
    assert "got: hello-from-stdin" in res.output
    assert res.metadata["had_stdin"] is True


@pytest.mark.asyncio
async def test_stdin_with_python_eval(term):
    """``python -c 'eval(input())'`` roundtrip."""
    res = await term.execute(
        "python -c \"import sys; print('reply=' + sys.stdin.read().strip())\"",
        stdin="ping\n",
    )
    assert res.success
    assert "reply=ping" in res.output


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_invokes_callback(term, tmp_path: Path):
    # Cross-platform: use python to emit lines with flush.
    (tmp_path / "emit.py").write_text(
        "import sys, time\n"
        "for i in range(1, 4):\n"
        "    print(f'line{i}', flush=True)\n",
        encoding="utf-8",
    )
    chunks: list = []

    def collect(text: str) -> None:
        chunks.append(text)

    res = await term.execute(
        "python emit.py",
        cwd=str(tmp_path),
        stream=True, on_stdout=collect,
    )
    assert res.success
    assert len(chunks) >= 1
    # The full output is still in res.output
    assert "line1" in res.output
    assert "line2" in res.output
    assert "line3" in res.output
    assert res.metadata["streamed"] is True


@pytest.mark.asyncio
async def test_streaming_combined_with_timeout(term, tmp_path: Path):
    # Cross-platform: python emits + sleeps.
    (tmp_path / "slow_emit.py").write_text(
        "import sys, time\n"
        "for i in range(1, 6):\n"
        "    print(i, flush=True); time.sleep(0.05)\n",
        encoding="utf-8",
    )
    seen: list = []

    def collect(text: str) -> None:
        seen.append(text)

    res = await term.execute(
        "python slow_emit.py",
        cwd=str(tmp_path),
        stream=True, on_stdout=collect, timeout_s=0.5,
    )
    assert res.success
    assert len(seen) >= 1
    assert "5" in res.output


@pytest.mark.asyncio
async def test_streaming_timeout_kills_process(term, tmp_path: Path):
    (tmp_path / "long_sleep.py").write_text(
        "import time; print('start', flush=True); time.sleep(10); print('end')",
        encoding="utf-8",
    )
    res = await term.execute(
        "python long_sleep.py",
        cwd=str(tmp_path),
        stream=True, timeout_s=0.3,
    )
    assert not res.success
    assert res.metadata["timed_out"] is True


# ---------------------------------------------------------------------------
# exit codes / metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_code_propagated_in_metadata(term, tmp_path: Path):
    (tmp_path / "exit42.py").write_text("import sys; sys.exit(42)\n", encoding="utf-8")
    res = await term.execute("python exit42.py", cwd=str(tmp_path))
    assert not res.success
    assert res.metadata["return_code"] == 42
    assert "duration_s" in res.metadata
    assert "command" in res.metadata


@pytest.mark.asyncio
async def test_command_metadata_truncates_long_input(term):
    long_cmd = "echo " + ("x" * 2000)
    res = await term.execute(long_cmd)
    assert res.success
    # Truncated to 500 chars
    assert len(res.metadata["command"]) == 500


@pytest.mark.asyncio
async def test_dangerous_command_still_blocked(term):
    res = await term.execute("rm -rf /")
    assert not res.success
    assert "safety" in (res.error or "").lower() or "deny" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_empty_command_returns_error(term):
    res = await term.execute("")
    assert not res.success
