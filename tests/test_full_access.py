"""Tests for the global "full access" switch (KAIROS_FULL_ACCESS / settings.fullAccess).

Two directions are pinned:

* **default** — every sandbox check still refuses exactly what it
  refused before: non-allowlisted heads, shell operators, paths outside
  the project, and the file tools' ``Path outside project directory``.
* **enabled** — both sources (env var and ``settings.json``) lift the
  confinement: an arbitrary command runs, a path outside the project is
  readable/writable, and a shell pipeline executes.

The minimal disk-destroy deny (``format`` / ``mkfs`` / ``diskpart`` /
``shred`` / ``bcdedit``) stays active even with the switch on.

All tests run offline against the local machine.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from kairos import settings_store as _ss
from kairos.access_control import is_full_access
from kairos.tools.cache import get_cache
from kairos.tools.file_edit import FileEditTool
from kairos.tools.file_read import FileReadTool
from kairos.tools.terminal import TerminalTool

PY = sys.executable
LIST_CMD = "dir" if os.name == "nt" else "ls"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No env switch, a throwaway settings store, and a clean tool cache."""
    monkeypatch.delenv("KAIROS_FULL_ACCESS", raising=False)
    _ss._store = _ss.SettingsStore(path=tmp_path / "settings.json")
    get_cache().clear()
    yield
    _ss._store = None
    get_cache().clear()


def _py_cmd(marker: str) -> str:
    """A command whose head is *not* on the terminal allow-list."""
    return f'"{PY}" -c "print(\'{marker}\')"'


# ---------------------------------------------------------------------------
# (a) default: behaviour is unchanged / still confined
# ---------------------------------------------------------------------------


def test_default_switch_is_off():
    assert is_full_access() is False


@pytest.mark.asyncio
async def test_default_terminal_rejects_non_allowlisted_head(tmp_path):
    term = TerminalTool(allowed_cwd=str(tmp_path / "ws"))
    res = await term.execute(_py_cmd("SHOULD_NOT_RUN"))
    assert not res.success
    assert "allowlist" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_default_terminal_rejects_shell_operator(tmp_path):
    term = TerminalTool(allowed_cwd=str(tmp_path))
    res = await term.execute("echo a && echo b")
    assert not res.success
    assert "shell operator" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_default_terminal_rejects_path_outside_cwd(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    term = TerminalTool(allowed_cwd=str(ws))
    res = await term.execute(f"{LIST_CMD} {outside}")
    assert not res.success
    assert "outside the project directory" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_default_file_read_rejects_outside_path(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = FileReadTool(allowed_root=ws)
    res = await tool.execute(path=str(outside))
    assert not res.success
    assert "Path outside project directory" in (res.error or "")


@pytest.mark.asyncio
async def test_default_file_write_rejects_outside_path(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tool = FileEditTool(allowed_root=ws)
    res = await tool.execute(path=str(tmp_path / "nope.txt"), content="x")
    assert not res.success
    assert "Path outside project directory" in (res.error or "")


# ---------------------------------------------------------------------------
# (b) enabled via the env var
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_switch_allows_arbitrary_command(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "1")
    assert is_full_access() is True
    term = TerminalTool(allowed_cwd=str(tmp_path / "ws"))
    res = await term.execute(_py_cmd("ENV_OK"))
    assert res.success, res.error
    assert "ENV_OK" in res.output


@pytest.mark.asyncio
async def test_env_switch_allows_shell_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "1")
    term = TerminalTool(allowed_cwd=str(tmp_path))
    res = await term.execute("echo a && echo b")
    assert res.success, res.error
    assert "a" in res.output
    assert "b" in res.output


@pytest.mark.asyncio
async def test_env_switch_allows_outside_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "true")
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "marker.txt").write_text("MARKER_XYZ", encoding="utf-8")
    term = TerminalTool(allowed_cwd=str(ws))
    res = await term.execute(f"{LIST_CMD} {outside}")
    assert res.success, res.error
    assert "marker" in res.output.lower()


@pytest.mark.asyncio
async def test_env_switch_allows_writing_outside_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "yes")
    ws = tmp_path / "ws"
    ws.mkdir()
    target = tmp_path / "outside_written.txt"
    tool = FileEditTool(allowed_root=ws)
    res = await tool.execute(path=str(target), content="hello outside")
    assert res.success, res.error
    assert target.read_text(encoding="utf-8") == "hello outside"


@pytest.mark.asyncio
async def test_env_switch_allows_reading_outside_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "readme.txt"
    outside.write_text("OUTSIDE_CONTENT", encoding="utf-8")
    tool = FileReadTool(allowed_root=ws)
    res = await tool.execute(path=str(outside))
    assert res.success, res.error
    assert "OUTSIDE_CONTENT" in res.output


# ---------------------------------------------------------------------------
# (b) enabled via settings.json:fullAccess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_switch_allows_arbitrary_command(tmp_path):
    _ss._store.update({"fullAccess": True})
    assert is_full_access() is True
    term = TerminalTool(allowed_cwd=str(tmp_path / "ws"))
    res = await term.execute(_py_cmd("SETTINGS_OK"))
    assert res.success, res.error
    assert "SETTINGS_OK" in res.output


@pytest.mark.asyncio
async def test_settings_switch_allows_shell_pipeline(tmp_path):
    _ss._store.update({"fullAccess": True})
    term = TerminalTool(allowed_cwd=str(tmp_path))
    res = await term.execute("echo x && echo y")
    assert res.success, res.error
    assert "x" in res.output
    assert "y" in res.output


def test_settings_field_roundtrips_through_store_and_dict(tmp_path):
    s = _ss.SettingsStore(path=tmp_path / "s.json")
    assert s.get().fullAccess is False
    s.update({"fullAccess": True})
    assert s.get().fullAccess is True
    assert _ss._to_dict(s.get())["fullAccess"] is True
    # survives a restart (re-read from disk)
    again = _ss.SettingsStore(path=tmp_path / "s.json")
    assert again.get().fullAccess is True
    # and turning it back off persists too
    again.update({"fullAccess": False})
    assert _ss.SettingsStore(path=tmp_path / "s.json").get().fullAccess is False


def test_settings_api_roundtrips_full_access(tmp_path):
    from fastapi.testclient import TestClient

    from api.app import app

    _ss._store = _ss.SettingsStore(path=tmp_path / "api_settings.json")
    with TestClient(app) as client:
        r = client.post("/api/projects/settings", json={"fullAccess": True})
        assert r.status_code == 200, r.text
        assert r.json()["fullAccess"] is True
        r2 = client.get("/api/projects/settings")
        assert r2.json()["fullAccess"] is True


# ---------------------------------------------------------------------------
# (c) the minimal disk-destroy deny survives full access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [
    "format C:",
    "mkfs.ext4 /dev/sda",
    "diskpart",
    "shred secret.txt",
    "bcdedit /set {default} bootstatuspolicy ignoreallfailures",
])
async def test_disk_destroy_commands_still_blocked_with_env(tmp_path, monkeypatch, cmd):
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "1")
    term = TerminalTool(allowed_cwd=str(tmp_path))
    res = await term.execute(cmd)
    assert not res.success, f"{cmd!r} should have been blocked"
    assert "safety" in (res.error or "").lower() or "deny" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_bare_format_head_still_blocked(tmp_path, monkeypatch):
    # ``format`` without a drive letter is caught by the retained head
    # check, not the regex deny-list.
    monkeypatch.setenv("KAIROS_FULL_ACCESS", "1")
    term = TerminalTool(allowed_cwd=str(tmp_path))
    res = await term.execute("format")
    assert not res.success
    assert "deny" in (res.error or "").lower() or "safety" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_disk_destroy_commands_still_blocked_with_settings(tmp_path):
    _ss._store.update({"fullAccess": True})
    term = TerminalTool(allowed_cwd=str(tmp_path))
    res = await term.execute("diskpart")
    assert not res.success
    assert "safety" in (res.error or "").lower() or "deny" in (res.error or "").lower()


# ---------------------------------------------------------------------------
# default still works for the allow-listed happy path (no over-tightening)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_allowlisted_command_still_runs(tmp_path):
    term = TerminalTool(allowed_cwd=str(tmp_path))
    res = await term.execute("echo hello_default")
    assert res.success, res.error
    assert "hello_default" in res.output
