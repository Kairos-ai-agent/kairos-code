"""Tests for the plugin system."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.plugins import (
    LoadedPlugin,
    MANIFEST_TEMPLATE,
    PLUGIN_MANIFEST,
    PluginError,
    PluginInfo,
    PluginManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin(
    root: Path,
    name: str,
    *,
    version: str = "0.1.0",
    with_skills: bool = False,
    with_mcp: bool = False,
    with_agents: bool = False,
    with_hooks: bool = False,
    with_manifest: bool = True,
) -> Path:
    """Create a minimal plugin directory and return its path."""
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    if with_manifest:
        (pdir / PLUGIN_MANIFEST).write_text(
            f"name: {name}\nversion: {version}\ndescription: test\n",
            encoding="utf-8",
        )
    if with_skills:
        sd = pdir / "skills"
        sd.mkdir(exist_ok=True)
        (sd / "demo.md").write_text(
            "---\n"
            "name: demo\n"
            "description: demo skill\n"
            "when:\n  keyword: demo\n"
            "---\n\n# demo body\n",
            encoding="utf-8",
        )
    if with_mcp:
        (pdir / "mcp.yaml").write_text(
            "mcp_servers:\n  demo_server:\n    command: foo\n",
            encoding="utf-8",
        )
    if with_agents:
        ad = pdir / "agents"
        ad.mkdir(exist_ok=True)
        (ad / "demo_agent.yaml").write_text(
            "name: demo\nsystem_prompt: |\n  You are demo.\n",
            encoding="utf-8",
        )
    if with_hooks:
        hd = pdir / "hooks"
        hd.mkdir(exist_ok=True)
        (hd / "hooks.json").write_text(
            '{"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}',
            encoding="utf-8",
        )
    return pdir


# ---------------------------------------------------------------------------
# list_installed + manifest parsing
# ---------------------------------------------------------------------------


def test_list_empty_when_no_plugins(tmp_path):
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    assert mgr.list_installed() == []


def test_list_returns_plugin_with_manifest(tmp_path):
    _make_plugin(tmp_path, "alpha")
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    plugins = mgr.list_installed()
    assert [p.name for p in plugins] == ["alpha"]
    assert plugins[0].version == "0.1.0"


def test_list_ignores_dirs_without_manifest(tmp_path):
    (tmp_path / "rogue").mkdir()
    _make_plugin(tmp_path, "alpha")
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    plugins = mgr.list_installed()
    # "rogue" has no manifest; we skip it.
    assert [p.name for p in plugins] == ["alpha"]


def test_list_ignores_bad_yaml_manifest(tmp_path):
    pdir = tmp_path / "broken"
    pdir.mkdir()
    (pdir / PLUGIN_MANIFEST).write_text("this: is: not: yaml", encoding="utf-8")
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    assert mgr.list_installed() == []


def test_list_disabled_flag_propagates(tmp_path):
    pdir = tmp_path / "alpha"
    pdir.mkdir()
    (pdir / PLUGIN_MANIFEST).write_text(
        "name: alpha\nenabled: false\n", encoding="utf-8"
    )
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    info = mgr.list_installed()[0]
    assert info.enabled is False


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------


def test_install_copies_plugin_directory(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    _make_plugin(src_root, "myplug", with_skills=True)

    dst_root = tmp_path / "dst"
    PluginManager(plugins_root=dst_root).install(src_root / "myplug")

    installed = dst_root / "myplug"
    assert installed.exists()
    assert (installed / PLUGIN_MANIFEST).exists()
    assert (installed / "skills" / "demo.md").exists()


def test_install_rejects_existing_name(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_plugin(src, "myplug")

    dst = tmp_path / "dst"
    dst.mkdir()
    mgr = PluginManager(plugins_root=dst)
    mgr.install(src / "myplug")
    with pytest.raises(PluginError):
        mgr.install(src / "myplug")  # already exists


def test_install_rejects_non_directory(tmp_path):
    bad = tmp_path / "not-a-dir.txt"
    bad.write_text("hi", encoding="utf-8")
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    with pytest.raises(PluginError):
        mgr.install(bad)


def test_uninstall_removes_directory(tmp_path):
    _make_plugin(tmp_path, "alpha")
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    assert mgr.uninstall("alpha") is True
    assert not (tmp_path / "alpha").exists()


def test_uninstall_returns_false_when_not_installed(tmp_path):
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    assert mgr.uninstall("nonexistent") is False


# ---------------------------------------------------------------------------
# load_all
# ---------------------------------------------------------------------------


def test_load_all_reports_what_each_plugin_offers(tmp_path):
    _make_plugin(tmp_path, "alpha", with_skills=True, with_mcp=True)
    _make_plugin(tmp_path, "beta", with_agents=True, with_hooks=True)
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    loaded = mgr.load_all()
    by_name = {p.info.name: p for p in loaded}
    assert by_name["alpha"].skills_dir is not None
    assert by_name["alpha"].mcp_file is not None
    assert by_name["beta"].agents_dir is not None
    assert by_name["beta"].hooks_file is not None


def test_load_all_skips_disabled_plugins(tmp_path):
    pdir = tmp_path / "alpha"
    pdir.mkdir()
    (pdir / PLUGIN_MANIFEST).write_text(
        "name: alpha\nenabled: false\n", encoding="utf-8"
    )
    (pdir / "skills").mkdir()
    (pdir / "skills" / "x.md").write_text("no skill", encoding="utf-8")
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    loaded = mgr.load_all()
    assert loaded == []


def test_load_all_partial_plugin_is_fine(tmp_path):
    """A plugin that ships only some directories should still load."""
    _make_plugin(tmp_path, "alpha", with_skills=True)  # no mcp, no agents
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    loaded = mgr.load_all()
    assert len(loaded) == 1
    p = loaded[0]
    assert p.skills_dir is not None
    assert p.mcp_file is None
    assert p.agents_dir is None


# ---------------------------------------------------------------------------
# aggregate_mcp_configs
# ---------------------------------------------------------------------------


def test_aggregate_mcp_configs_merges_servers(tmp_path):
    _make_plugin(tmp_path, "alpha", with_mcp=True)
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    plugins = mgr.load_all()
    merged = PluginManager.aggregate_mcp_configs(plugins)
    assert "demo_server" in merged["mcp_servers"]


def test_aggregate_mcp_configs_handles_no_plugins():
    merged = PluginManager.aggregate_mcp_configs([])
    assert merged == {"mcp_servers": {}}


def test_aggregate_mcp_configs_skips_bad_yaml(tmp_path):
    pdir = tmp_path / "alpha"
    pdir.mkdir()
    (pdir / PLUGIN_MANIFEST).write_text(
        "name: alpha\n", encoding="utf-8"
    )
    (pdir / "mcp.yaml").write_text(
        "not: valid: yaml: [[[", encoding="utf-8"
    )
    mgr = PluginManager(plugins_root=tmp_path,
                        bundled_root=tmp_path / "no_bundled_here")
    plugins = mgr.load_all()
    # Bad yaml is logged and skipped; result is the empty default.
    merged = PluginManager.aggregate_mcp_configs(plugins)
    assert merged == {"mcp_servers": {}}


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


def test_manifest_template_includes_documented_keys():
    for k in ("name", "version", "description", "skills", "agents",
              "hooks", "mcp", "commands"):
        assert k in MANIFEST_TEMPLATE
