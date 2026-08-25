"""Tests for hierarchical config merge."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.config.merge import deep_merge, hierarchical_search_paths, merge_configs


# ---------------------------------------------------------------------------
# deep_merge
# ---------------------------------------------------------------------------


def test_deep_merge_dicts_recurse():
    assert deep_merge(
        {"a": {"b": 1, "c": 2}},
        {"a": {"c": 99, "d": 3}},
    ) == {"a": {"b": 1, "c": 99, "d": 3}}


def test_deep_merge_lists_replace_not_extend():
    out = deep_merge({"items": [1, 2, 3]}, {"items": [9]})
    assert out == {"items": [9]}


def test_deep_merge_scalars_replace():
    assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"b": 1}}
    override = {"a": {"c": 2}}
    out = deep_merge(base, override)
    # The inputs must be unchanged.
    assert base == {"a": {"b": 1}}
    assert override == {"a": {"c": 2}}
    # The output is a new dict; mutating it doesn't affect base.
    out["a"]["b"] = 99
    assert base["a"]["b"] == 1


def test_deep_merge_handles_empty():
    assert deep_merge({}, {"a": 1}) == {"a": 1}
    assert deep_merge({"a": 1}, {}) == {"a": 1}
    assert deep_merge({}, {}) == {}


def test_deep_merge_nested_dicts():
    out = deep_merge(
        {"a": {"b": {"c": 1, "d": 2}}},
        {"a": {"b": {"c": 99}}},
    )
    assert out == {"a": {"b": {"c": 99, "d": 2}}}


# ---------------------------------------------------------------------------
# merge_configs
# ---------------------------------------------------------------------------


def test_merge_configs_no_files_uses_defaults():
    out = merge_configs([], defaults={"x": 1})
    assert out == {"x": 1}


def test_merge_configs_uses_default_when_path_missing(tmp_path):
    out = merge_configs(
        [tmp_path / "missing.yaml"],
        defaults={"x": 1},
    )
    assert out == {"x": 1}


def test_merge_configs_single_file_overrides_defaults(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("x: 2\n", encoding="utf-8")
    out = merge_configs([p], defaults={"x": 1, "y": 3})
    assert out == {"x": 2, "y": 3}


def test_merge_configs_later_paths_win(tmp_path):
    p1 = tmp_path / "a.yaml"
    p1.write_text("x: 1\ny: 1\n", encoding="utf-8")
    p2 = tmp_path / "b.yaml"
    p2.write_text("x: 2\n", encoding="utf-8")
    out = merge_configs([p1, p2])
    assert out == {"x": 2, "y": 1}


def test_merge_configs_bad_yaml_skipped(tmp_path):
    p1 = tmp_path / "good.yaml"
    p1.write_text("x: 1\n", encoding="utf-8")
    p2 = tmp_path / "bad.yaml"
    p2.write_text("this: is: not: valid: yaml: [[[[", encoding="utf-8")
    p3 = tmp_path / "later.yaml"
    p3.write_text("y: 2\n", encoding="utf-8")
    out = merge_configs([p1, p2, p3])
    assert out == {"x": 1, "y": 2}


def test_merge_configs_non_mapping_root_ignored(tmp_path):
    p = tmp_path / "list_root.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    out = merge_configs([p], defaults={"x": 1})
    assert out == {"x": 1}


# ---------------------------------------------------------------------------
# hierarchical_search_paths
# ---------------------------------------------------------------------------


def test_hierarchical_search_paths_user_and_project(tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    paths = hierarchical_search_paths(
        project_dir=proj, user_dir=user, file_name="settings.yaml"
    )
    assert paths == [
        user / "settings.yaml",
        proj / ".kairos" / "settings.yaml",
    ]


def test_hierarchical_search_paths_with_plugins(tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    plg1 = tmp_path / "plugins" / "alpha"
    plg2 = tmp_path / "plugins" / "beta"
    paths = hierarchical_search_paths(
        project_dir=proj, user_dir=user, file_name="mcp.yaml",
        extra_plugin_dirs=[plg1, plg2],
    )
    # user first, project second, plugins last (highest priority).
    assert paths == [
        user / "mcp.yaml",
        proj / ".kairos" / "mcp.yaml",
        plg1 / "mcp.yaml",
        plg2 / "mcp.yaml",
    ]


def test_hierarchical_search_paths_default_user_dir(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    paths = hierarchical_search_paths(project_dir=proj, file_name="x.yaml")
    # No user_dir supplied → defaults to ~/.kairos
    assert paths[0] == Path.home() / ".kairos" / "x.yaml"
    assert paths[1] == proj / ".kairos" / "x.yaml"
