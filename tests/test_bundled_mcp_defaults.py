"""R38.12 — the servers a fresh install can already use.

"Pre-installed" has to mean *working*, not *present*. So most of this file is
about the merge (a user entry must be able to override a shipped default), and
the last test actually starts the five shipped servers and asks them for their
tools — the difference between a config that looks right and one that runs.
"""

import asyncio
import time
from pathlib import Path

import pytest

from kairos.mcp_client import (McpServerConfig, _merged_servers, load_configs)
from kairos.mcp_local_servers import BUNDLED_SERVERS, resolve_bundled
from kairos.plugins import PluginManager

BUNDLED_PLUGIN = "kairos-essentials"


@pytest.fixture(autouse=True)
def _allow_bundled_defaults(monkeypatch):
    """This file is about the shipped defaults; the suite-wide fuse must be off.

    tests/conftest.py sets KAIROS_NO_BUNDLED_MCP so the rest of the suite never
    spawns real servers. These tests exist to prove those defaults work.
    """
    monkeypatch.delenv("KAIROS_NO_BUNDLED_MCP", raising=False)


def _no_user_config(tmp_path: Path) -> Path:
    """A user dir that exists but has no mcp.yaml."""
    user = tmp_path / "home" / ".kairos"
    user.mkdir(parents=True, exist_ok=True)
    return user


# ---------------------------------------------------------------------------
# the bundled plugin itself
# ---------------------------------------------------------------------------

def test_the_bundled_plugin_is_shipped_and_labelled():
    manager = PluginManager()
    bundled = {p.name: p for p in manager.list_bundled()}
    assert BUNDLED_PLUGIN in bundled, sorted(bundled)
    info = bundled[BUNDLED_PLUGIN]
    assert info.bundled is True
    assert info.enabled is True
    assert "mcp" in info.capabilities
    # It must not need the user's directory to exist.
    assert info.path.is_dir()

    loaded = {p.info.name: p for p in manager.load_all()}
    assert BUNDLED_PLUGIN in loaded
    assert loaded[BUNDLED_PLUGIN].mcp_file is not None


def test_a_user_plugin_of_the_same_name_wins(tmp_path: Path):
    """Shipping a plugin must never stop anyone replacing it."""
    user_root = tmp_path / "plugins"
    (user_root / BUNDLED_PLUGIN).mkdir(parents=True)
    (user_root / BUNDLED_PLUGIN / "kairos-plugin.yaml").write_text(
        "name: kairos-essentials\nversion: 9.9.9\nenabled: false\n",
        encoding="utf-8")
    manager = PluginManager(plugins_root=user_root)
    loaded = {p.info.name: p for p in manager.load_all()}
    # Disabled in the user scope ⇒ gone entirely, bundled copy does not resurface.
    assert BUNDLED_PLUGIN not in loaded
    assert manager.list_bundled(), "the shipped copy is still listed as bundled"


# ---------------------------------------------------------------------------
# the merge
# ---------------------------------------------------------------------------

def test_a_fresh_install_already_has_the_offline_servers(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    configs = load_configs(project_dir=project,
                           user_dir=_no_user_config(tmp_path))

    assert set(configs) == set(BUNDLED_SERVERS), sorted(configs)
    for name, cfg in configs.items():
        assert cfg.enabled is True, name
        assert cfg.transport == "stdio", name
        assert cfg.source == "bundled-plugin", name
        assert cfg.command, name
        assert cfg.args, name


def test_the_filesystem_server_is_rooted_at_the_project(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    configs = load_configs(project_dir=project,
                           user_dir=_no_user_config(tmp_path))
    args = configs["filesystem"].args
    assert str(project.resolve()) in args, args
    # And it is the real launcher for this install, not a hand-written path.
    expected = resolve_bundled("filesystem", project)
    assert configs["filesystem"].command == expected["command"]


def test_a_user_entry_overrides_a_shipped_default(tmp_path: Path):
    user = tmp_path / "home" / ".kairos"
    user.mkdir(parents=True)
    (user / "mcp.yaml").write_text(
        "mcp_servers:\n"
        "  time:\n"
        "    enabled: false\n"
        "  fetch:\n"
        "    enabled: true\n"
        "    env:\n"
        "      HTTP_PROXY: http://127.0.0.1:8888\n",
        encoding="utf-8")
    configs = load_configs(project_dir=tmp_path, user_dir=user)

    assert configs["time"].enabled is False
    assert configs["time"].source == "user"
    # Overriding one field keeps the rest of the shipped entry (deep merge).
    assert configs["time"].command, "the bundled command was lost in the merge"
    assert configs["fetch"].env.get("HTTP_PROXY", "").endswith("8888")
    # Untouched defaults are still there.
    assert configs["git"].source == "bundled-plugin"


def test_a_project_entry_beats_the_user_entry(tmp_path: Path):
    user = tmp_path / "home" / ".kairos"
    user.mkdir(parents=True)
    (user / "mcp.yaml").write_text(
        "mcp_servers:\n  sqlite:\n    enabled: false\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".kairos").mkdir(parents=True)
    (project / ".kairos" / "mcp.yaml").write_text(
        "mcp_servers:\n  sqlite:\n    enabled: true\n", encoding="utf-8")

    configs = load_configs(project_dir=project, user_dir=user)
    assert configs["sqlite"].enabled is True
    assert configs["sqlite"].source == "project"


def test_a_bad_placeholder_does_not_break_the_install(tmp_path: Path):
    """No project dir ⇒ {project_dir} is left alone rather than exploding."""
    merged = _merged_servers(project_dir=None, user_dir=_no_user_config(tmp_path))
    assert "filesystem" in merged
    assert "{project_dir}" in str(merged["filesystem"].get("root", ""))


def test_the_defaults_can_be_skipped(tmp_path: Path):
    merged = _merged_servers(project_dir=tmp_path,
                             user_dir=_no_user_config(tmp_path),
                             include_bundled=False)
    assert merged == {}


# ---------------------------------------------------------------------------
# and they really run
# ---------------------------------------------------------------------------

@pytest.mark.timeout(180)
def test_the_shipped_servers_actually_start_and_offer_tools(tmp_path: Path):
    """The claim is "no npm, no pip, no network". Ask the servers."""
    from kairos.mcp_client import McpRegistry

    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print('hi')\n", encoding="utf-8")

    registry = McpRegistry()
    registry.load(project_dir=project, user_dir=_no_user_config(tmp_path))
    assert len(registry._configs) == len(BUNDLED_SERVERS)

    async def go():
        try:
            await registry.start_all()
            return registry.all_tools()
        finally:
            await registry.close_all()

    tools = asyncio.run(go())
    names = {t.name for t in tools}
    assert registry.startup_errors == {}, registry.startup_errors

    # Five servers, and every one of them contributed something.
    servers = {t.name.split("__")[0] for t in tools if "__" in t.name}
    assert len(servers) >= 4, (sorted(servers), sorted(names))
    assert any("time_now" in n for n in names), sorted(names)
    assert any("read_file" in n for n in names), sorted(names)
    assert any("git_status" in n or "git_log" in n for n in names), sorted(names)


def _pid_is_alive(pid: int) -> bool:
    """Is this PID still there? Cheap and dependency-free on both platforms."""
    import os
    import subprocess
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=20).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


@pytest.mark.timeout(180)
def test_nothing_is_left_running_when_the_registry_closes(tmp_path: Path):
    """A started server that outlives its registry is a leaked process.

    This project has been bitten by exactly that three times (hooks, the smoke
    script, the loop's precheck), so this test asks the OS rather than trusting
    the client's own bookkeeping.
    """
    import asyncio as _asyncio

    from kairos.mcp_client import McpRegistry

    project = tmp_path / "project"
    project.mkdir()
    registry = McpRegistry()
    registry.load(project_dir=project, user_dir=_no_user_config(tmp_path))

    async def go():
        await registry.start_all()
        pids = []
        for client in registry._clients.values():
            proc = getattr(client, "_process", None)
            if proc is not None and proc.pid:
                pids.append(proc.pid)
        await registry.close_all()
        return pids

    pids = _asyncio.run(go())
    assert len(pids) == len(BUNDLED_SERVERS), pids
    time.sleep(1.0)  # let the OS reap them
    still_alive = [pid for pid in pids if _pid_is_alive(pid)]
    assert not still_alive, f"leaked MCP children: {still_alive}"
