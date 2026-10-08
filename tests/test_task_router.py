"""Routing: which path does a task take -- the code loop, or the skeleton?

The safety property under test is that the router is *one-sided*: it only ever
sends a task to the skeleton when the caller explicitly says so or the
workspace positively looks like a document set. Everything else -- including
"cannot tell" -- resolves to the loop, i.e. today's behaviour, unchanged.

Priority, in order: explicit signal > heuristic > default (loop).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kairos.task_router import (
    ROUTE_LOOP,
    ROUTE_SKELETON,
    RouteDecision,
    WS_DOCS,
    WS_REPO,
    route_task,
    scan_workspace,
)


# ---------------------------------------------------------------------------
# helpers: build workspaces with a specific shape
# ---------------------------------------------------------------------------

def _docs_only(root: Path) -> Path:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "alpha.md").write_text("甲：延迟 30ms", encoding="utf-8")
    (root / "docs" / "beta.md").write_text("乙：延迟 80ms", encoding="utf-8")
    return root


def _code_repo(root: Path) -> Path:
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# explicit signal wins outright
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["docs", "doc", "DOCS", "report", "non-code", "docset"])
def test_explicit_docs_kind_routes_to_the_skeleton(tmp_path, value):
    d = route_task(explicit_kind=value, workspace=tmp_path)
    assert d.route == ROUTE_SKELETON
    assert d.workspace_kind == WS_DOCS
    assert d.source == "explicit"
    assert d.uses_skeleton is True
    # the reason names the signal so a log line can explain the decision
    assert value.lower() in d.reason.lower() or "explicit" in d.reason


@pytest.mark.parametrize("value", ["repo", "code", "bugfix", "feature"])
def test_explicit_repo_kind_routes_to_the_loop(tmp_path, value):
    # note: the workspace *looks like docs*, but the explicit signal wins
    _docs_only(tmp_path)
    d = route_task(explicit_kind=value, workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.workspace_kind == WS_REPO
    assert d.source == "explicit"


def test_explicit_docs_beats_a_repo_workspace(tmp_path):
    """An explicit document task does not need a docs-only directory."""
    _code_repo(tmp_path)  # has pyproject.toml + a .py file -> heuristic says repo
    d = route_task(explicit_kind="docs", workspace=tmp_path)
    assert d.route == ROUTE_SKELETON
    assert d.source == "explicit"


def test_metadata_kind_is_an_explicit_signal(tmp_path):
    d = route_task(metadata={"kind": "docs"}, workspace=tmp_path)
    assert d.route == ROUTE_SKELETON and d.workspace_kind == WS_DOCS

    d2 = route_task(metadata={"workspace_kind": "repo"}, workspace=_docs_only(tmp_path))
    assert d2.route == ROUTE_LOOP and d2.workspace_kind == WS_REPO


def test_explicit_param_beats_metadata(tmp_path):
    d = route_task(explicit_kind="repo", metadata={"kind": "docs"}, workspace=tmp_path)
    assert d.route == ROUTE_LOOP


def test_unrecognized_explicit_value_falls_through(tmp_path):
    """A caller's typo must not silently reroute -- it falls to the default."""
    d = route_task(explicit_kind="banana", workspace=tmp_path)  # empty dir too
    assert d.route == ROUTE_LOOP
    assert d.source == "default"


# ---------------------------------------------------------------------------
# the heuristic: conservative, one-sided
# ---------------------------------------------------------------------------

def test_heuristic_routes_a_document_set_to_the_skeleton(tmp_path):
    _docs_only(tmp_path)
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_SKELETON
    assert d.workspace_kind == WS_DOCS
    assert d.source == "heuristic"
    assert d.signals["doc_files"] >= 2
    assert d.signals["code_files"] == 0
    assert d.signals["has_git"] is False


@pytest.mark.parametrize("builder", [_code_repo])
def test_heuristic_routes_an_engineering_workspace_to_the_loop(tmp_path, builder):
    builder(tmp_path)
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.workspace_kind == WS_REPO
    assert d.source == "heuristic"


def test_a_git_dir_alone_is_a_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP and d.workspace_kind == WS_REPO


def test_a_bare_source_file_is_a_repo(tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP


def test_docs_plus_code_is_still_a_repo(tmp_path):
    """Code wins: a repo that also has a docs/ folder stays on the loop."""
    _docs_only(tmp_path)
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP and d.workspace_kind == WS_REPO


def test_a_test_directory_is_an_engineering_marker(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP


# ---------------------------------------------------------------------------
# default: undecidable -> the loop, exactly as today
# ---------------------------------------------------------------------------

def test_empty_workspace_is_undecided_and_defaults_to_the_loop(tmp_path):
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "default"
    assert d.workspace_kind == WS_REPO


def test_no_workspace_at_all_defaults_to_the_loop():
    d = route_task()
    assert d.route == ROUTE_LOOP
    assert d.source == "default"


def test_a_missing_directory_defaults_to_the_loop(tmp_path):
    d = route_task(workspace=tmp_path / "does-not-exist")
    assert d.route == ROUTE_LOOP
    assert d.signals["scanned"] is False


def test_workspace_with_only_unknown_files_is_undecided(tmp_path):
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    (tmp_path / "notes").write_text("no extension", encoding="utf-8")
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP and d.source == "default"


# ---------------------------------------------------------------------------
# the decision is inspectable data
# ---------------------------------------------------------------------------

def test_scan_workspace_reports_its_findings(tmp_path):
    _code_repo(tmp_path)
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    s = scan_workspace(tmp_path)
    assert s["scanned"] is True
    assert s["code_files"] == 1 and s["doc_files"] >= 1
    assert "pyproject.toml" in s["engineering_markers"]


def test_decision_dict_is_loggable(tmp_path):
    _docs_only(tmp_path)
    d = route_task(workspace=tmp_path)
    payload = d.to_dict()
    assert payload["route"] == ROUTE_SKELETON
    assert payload["workspace_kind"] == WS_DOCS
    assert payload["source"] == "heuristic"
    assert isinstance(payload["signals"], dict) and payload["reason"]


def test_large_workspace_scan_is_bounded(tmp_path):
    """The scan never walks an unbounded tree -- it stops at max_files."""
    s = scan_workspace(tmp_path, max_files=3)  # empty dir, just must not hang
    assert s["scanned"] is True
    for i in range(50):
        (tmp_path / f"f{i}.py").write_text("x=1\n", encoding="utf-8")
    s2 = scan_workspace(tmp_path, max_files=3)
    assert s2["total_files"] <= 3


# ---------------------------------------------------------------------------
# KAIROS_ROUTE_DEFAULT: the *undecided* lane can be configured, default loop
# ---------------------------------------------------------------------------

from kairos.task_router import ROUTE_DEFAULT_ENV, default_route  # noqa: E402


def _clear_route_default(monkeypatch):
    monkeypatch.delenv(ROUTE_DEFAULT_ENV, raising=False)


def test_default_route_is_loop_when_unset(monkeypatch):
    _clear_route_default(monkeypatch)
    assert default_route() == ROUTE_LOOP


@pytest.mark.parametrize("value", ["loop", "LOOP", " loop ", "", "   ", "banana", "skel", "skeletonn"])
def test_default_route_is_loop_for_everything_but_the_exact_word(monkeypatch, value):
    """Unset, 'loop', a typo -- all keep the historical default, unchanged."""
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, value)
    assert default_route() == ROUTE_LOOP


@pytest.mark.parametrize("value", ["skeleton", "SKELETON", " Skeleton "])
def test_default_route_is_skeleton_only_for_the_exact_word(monkeypatch, value):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, value)
    assert default_route() == ROUTE_SKELETON


def test_undecided_default_stays_loop_without_the_env(tmp_path, monkeypatch):
    """Baseline: with nothing set, the undecided case is today's loop, verbatim."""
    _clear_route_default(monkeypatch)
    d = route_task(workspace=tmp_path)  # empty dir -> undecided
    assert d.route == ROUTE_LOOP
    assert d.source == "default"
    assert d.workspace_kind == WS_REPO
    # the reason text is the unchanged one
    assert "unchanged behaviour" in d.reason


def test_undecided_task_routes_to_skeleton_when_env_is_skeleton(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "skeleton")
    d = route_task(workspace=tmp_path)  # empty dir -> undecided
    assert d.route == ROUTE_SKELETON
    assert d.uses_skeleton is True
    assert d.source == "default"
    # workspace_kind is unchanged from today's default branch (a repo default)
    assert d.workspace_kind == WS_REPO
    assert ROUTE_DEFAULT_ENV in d.reason


def test_skeleton_default_does_not_reroute_an_explicit_repo(tmp_path, monkeypatch):
    """An explicit code signal outranks the configured default."""
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "skeleton")
    _docs_only(tmp_path)  # would heuristically be docs
    d = route_task(explicit_kind="repo", workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "explicit"


def test_skeleton_default_does_not_reroute_an_explicit_docs(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "skeleton")
    d = route_task(explicit_kind="docs", workspace=tmp_path)
    assert d.route == ROUTE_SKELETON and d.source == "explicit"


def test_skeleton_default_leaves_the_heuristic_repo_on_the_loop(tmp_path, monkeypatch):
    """A decided-``repo`` workspace stays on the loop even under the override."""
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "skeleton")
    _code_repo(tmp_path)
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "heuristic"
    assert d.workspace_kind == WS_REPO


def test_skeleton_default_still_sends_a_docs_workspace_to_the_skeleton(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "skeleton")
    _docs_only(tmp_path)
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_SKELETON and d.source == "heuristic"


def test_loop_value_keeps_undecided_on_the_loop(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "loop")
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP and d.source == "default"
