"""Tests for project manifest (Codex-Harness-style project config)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
import yaml

from kairos.manifest import (
    DEFAULTS,
    Manifest,
    WorkspaceSpec,
    TrustSpec,
    SandboxSpec,
    AgentsSpec,
    ReviewersSpec,
    ModelRouterSpec,
    _deep_merge,
    load,
    render_template,
)


def test_deep_merge_lists_replace():
    base = {"a": [1, 2, 3], "b": {"c": 1}}
    out = _deep_merge(base, {"a": [9]})
    assert out["a"] == [9]  # not [9, 1, 2, 3]
    assert out["b"] == {"c": 1}


def test_deep_merge_dicts_recurse():
    out = _deep_merge(
        {"x": {"a": 1, "b": 2}}, {"x": {"b": 99, "c": 3}}
    )
    assert out["x"] == {"a": 1, "b": 99, "c": 3}


def test_load_defaults_when_no_file():
    m = load(project_dir=None)
    assert isinstance(m, Manifest)
    assert m.source_path is None
    # Defaults should match the DEFAULTS table for the most-likely
    # surface area.
    assert m.agents.max_concurrent == DEFAULTS["agents"]["max_concurrent"]
    assert m.sandbox.network is False
    assert "reviewer" in m.reviewers.enabled


def test_load_partial_manifest_uses_defaults_for_missing():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        kairos_dir = proj / ".kairos"
        kairos_dir.mkdir()
        # Only override agents.max_concurrent.
        (kairos_dir / "manifest.yaml").write_text(
            "agents:\n  max_concurrent: 8\n", encoding="utf-8"
        )
        m = load(project_dir=proj)
        assert m.source_path == kairos_dir / "manifest.yaml"
        assert m.agents.max_concurrent == 8
        # Untouched fields still match defaults.
        assert m.agents.max_tool_turns == DEFAULTS["agents"]["max_tool_turns"]
        assert m.sandbox.network == DEFAULTS["sandbox"]["network"]


def test_load_full_manifest():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        kairos_dir = proj / ".kairos"
        kairos_dir.mkdir()
        (kairos_dir / "manifest.yaml").write_text(
            yaml.safe_dump({
                "workspace": {"name": "demo", "type": "node", "entry": "index.js"},
                "trust": {
                    "paths": ["src/**"],
                    "deny": [".env", "*.secret"],
                },
                "sandbox": {"network": True, "memory_mb": 1024, "timeout_s": 60},
                "agents": {"max_concurrent": 4, "max_tool_turns": 25},
                "reviewers": {
                    "enabled": ["reviewer", "security_reviewer"],
                    "weights": {"reviewer": 0.7, "security_reviewer": 0.3},
                },
                "model_router": {"role_models": {"coder": "creative"}},
            }),
            encoding="utf-8",
        )
        m = load(project_dir=proj)
        assert m.workspace.name == "demo"
        assert m.workspace.type == "node"
        assert m.workspace.entry == "index.js"
        assert m.trust.paths == ["src/**"]
        assert m.trust.deny == [".env", "*.secret"]
        assert m.sandbox.network is True
        assert m.sandbox.memory_mb == 1024
        assert m.agents.max_concurrent == 4
        assert m.reviewers.enabled == ["reviewer", "security_reviewer"]
        assert m.model_router.role_models == {"coder": "creative"}


def test_load_malformed_yaml_falls_back_to_defaults():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        kairos_dir = proj / ".kairos"
        kairos_dir.mkdir()
        (kairos_dir / "manifest.yaml").write_text(
            "this: is: not: valid: yaml: [[[[", encoding="utf-8"
        )
        m = load(project_dir=proj)
        # Should NOT raise; should fall back to defaults.
        assert m.source_path is None
        assert m.agents.max_concurrent == DEFAULTS["agents"]["max_concurrent"]


def test_load_root_must_be_mapping():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        kairos_dir = proj / ".kairos"
        kairos_dir.mkdir()
        (kairos_dir / "manifest.yaml").write_text("- a\n- b\n", encoding="utf-8")
        m = load(project_dir=proj)
        assert m.source_path is None


def test_explicit_manifest_path_overrides_project_dir():
    with tempfile.TemporaryDirectory() as d:
        custom = Path(d) / "my_manifest.yaml"
        custom.write_text("agents:\n  max_concurrent: 99\n", encoding="utf-8")
        m = load(project_dir=Path(d) / "nonexistent", manifest_path=custom)
        assert m.agents.max_concurrent == 99
        assert m.source_path == custom


def test_manifest_to_dict_round_trip():
    m = load()
    d = m.to_dict()
    assert d["workspace"]["type"] == "python"
    assert d["agents"]["max_concurrent"] >= 1
    # Round-trip: load it again from a synthesized source and confirm
    # values survive.
    again = Manifest(**{
        "workspace": WorkspaceSpec(**d["workspace"]),
        "trust": TrustSpec(**d["trust"]),
        "sandbox": SandboxSpec(**d["sandbox"]),
        "agents": AgentsSpec(**d["agents"]),
        "reviewers": ReviewersSpec(**d["reviewers"]),
        "model_router": ModelRouterSpec(**d["model_router"]),
        "source_path": None,
    })
    assert again.agents.max_concurrent == m.agents.max_concurrent
    assert again.reviewers.enabled == m.reviewers.enabled


def test_render_template_includes_all_sections():
    text = render_template()
    for section in (
        "workspace:", "trust:", "sandbox:", "agents:",
        "reviewers:", "model_router:",
    ):
        assert section in text, f"missing {section} in template"


def test_trust_deny_uses_defaults_when_omitted():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        kairos_dir = proj / ".kairos"
        kairos_dir.mkdir()
        # Override only workspace, leave trust alone.
        (kairos_dir / "manifest.yaml").write_text(
            "workspace:\n  name: x\n", encoding="utf-8"
        )
        m = load(project_dir=proj)
        assert ".env" in m.trust.deny


def test_reviewers_weights_default_to_normalized_set():
    m = load()
    s = sum(m.reviewers.weights.values())
    # Defaults are 0.5+0.3+0.2 = 1.0. Custom sets may not sum to 1;
    # this is a sanity check that we're not shipping weights that
    # accidentally double-count.
    assert abs(s - 1.0) < 0.05
