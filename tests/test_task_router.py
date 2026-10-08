"""Routing: which path does a task take -- the code loop, or the general lane?

The router is now two-sided but still conservative: it sends a task to the
**loop** when there is a positive code signal (an explicit ``kind=repo``, a
multi-step / plan-mode "long task", a coding-intent word in the message, or a
workspace that looks like a repo), and to the **general lane (skeleton)**
otherwise. An undecided task (no signal at all) now defaults to the general
lane -- "平时 chat 走通用" -- and ``KAIROS_ROUTE_DEFAULT=loop`` restores the
historical loop default one process at a time.

Priority, in order: explicit kind > long task > coding intent > workspace
heuristic > default.

The coding-intent vocabulary is a deliberately crude, in-code, auditable list;
it WILL misfire on prose that merely mentions a code word, and that is by
design (see ``kairos/task_router.CODING_INTENT_TERMS``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kairos.task_router import (
    CODING_INTENT_TERMS,
    ROUTE_DEFAULT_ENV,
    ROUTE_LOOP,
    ROUTE_SKELETON,
    RouteDecision,
    WS_DOCS,
    WS_REPO,
    default_route,
    detect_coding_intent,
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


def test_explicit_repo_kind_in_a_docs_workspace_routes_to_the_loop(tmp_path):
    """(test 5) An explicit code signal keeps the loop even for a docs root."""
    _docs_only(tmp_path)
    d = route_task(explicit_kind="repo", workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "explicit"
    assert d.workspace_kind == WS_REPO


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


def test_unrecognized_explicit_value_falls_to_the_default_lane(tmp_path):
    """A caller's typo must not silently reroute -- it falls to the default."""
    d = route_task(explicit_kind="banana", workspace=tmp_path)  # empty dir too
    assert d.route == ROUTE_SKELETON          # the (new) default lane
    assert d.source == "default"


# ---------------------------------------------------------------------------
# the workspace heuristic: conservative, code wins
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
# (test 3) a code repo with a *plain question* still takes the loop,
#          byte-for-byte today's coder path
# ---------------------------------------------------------------------------

def test_code_repo_workspace_with_a_plain_question_takes_the_loop(tmp_path):
    _code_repo(tmp_path)
    d = route_task(requirement="这个项目是做什么的？", workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "heuristic"          # the workspace decided, not the text
    assert d.workspace_kind == WS_REPO


# ---------------------------------------------------------------------------
# (test 4) a docs project with a plain question takes the general lane
# ---------------------------------------------------------------------------

def test_docs_workspace_with_a_plain_question_takes_the_general_lane(tmp_path):
    _docs_only(tmp_path)
    d = route_task(requirement="帮我总结一下这几份文档", workspace=tmp_path)
    assert d.route == ROUTE_SKELETON
    assert d.source == "heuristic"
    assert d.workspace_kind == WS_DOCS


# ---------------------------------------------------------------------------
# (test 1) plain small talk with no signal -> the general lane (new default)
# ---------------------------------------------------------------------------

def test_plain_smalltalk_on_an_undecided_workspace_takes_the_general_lane(tmp_path):
    d = route_task(requirement="你好，今天天气怎么样？", workspace=tmp_path)
    assert d.route == ROUTE_SKELETON
    assert d.source == "default"
    assert d.uses_skeleton is True


# ---------------------------------------------------------------------------
# (test 2) coding intent -> the loop, even on an ambiguous workspace
# ---------------------------------------------------------------------------

def test_chinese_coding_intent_routes_to_the_loop(tmp_path):
    d = route_task(requirement="修复 src/auth 里的登录 bug", workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "coding_intent"
    assert d.signals["coding_intent"]
    assert "coding intent" in d.reason


@pytest.mark.parametrize("text", [
    "fix the auth bug",
    "please refactor this module",
    "implement the endpoint",
    "debug why the test fails",
    "add a function to parse it",
    "commit and merge this branch",
])
def test_english_coding_intent_routes_to_the_loop(tmp_path, text):
    d = route_task(requirement=text, workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "coding_intent"


def test_coding_intent_beats_a_docs_workspace(tmp_path):
    """A coding word outranks the docs heuristic (intent > workspace shape)."""
    _docs_only(tmp_path)
    d = route_task(requirement="重构这里的代码", workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "coding_intent"


def test_coding_vocabulary_is_in_code_and_auditable():
    # the exact examples the task named must be present in the fixed list
    for term in ("修", "改", "实现", "重构", "测试", "报错", "异常", "函数",
                 "文件", "提交", "commit", "refactor", "implement", "debug",
                 "bug", "test", "function"):
        assert term in CODING_INTENT_TERMS, term


def test_ascii_coding_terms_use_a_leading_word_boundary():
    # 'code' must not match inside 'decode' (a leading word boundary)
    assert detect_coding_intent("please decode this string") is None
    assert detect_coding_intent("commit the change") == "commit"


def test_detect_coding_intent_is_safe_on_odd_inputs():
    assert detect_coding_intent(None) is None
    assert detect_coding_intent("") is None
    assert detect_coding_intent(12345) is None
    assert detect_coding_intent("今天天气不错") is None


# ---------------------------------------------------------------------------
# (test: long-task signal) a multi-step / plan-mode task -> the loop
# ---------------------------------------------------------------------------

def test_long_task_flag_routes_to_the_loop(tmp_path):
    d = route_task(workspace=tmp_path, long_task=True)
    assert d.route == ROUTE_LOOP
    assert d.source == "long_task"
    assert d.signals["long_task"] is True


def test_plan_mode_metadata_is_a_long_task_signal(tmp_path):
    d = route_task(workspace=tmp_path, metadata={"require_plan": True})
    assert d.route == ROUTE_LOOP and d.source == "long_task"


def test_a_plan_id_is_a_long_task_signal(tmp_path):
    d = route_task(workspace=tmp_path, metadata={"plan_id": "p-123"})
    assert d.route == ROUTE_LOOP and d.source == "long_task"


def test_a_plan_id_alone_is_a_long_task_signal():
    # no workspace, but a plan id -> the loop, named as a long task
    d = route_task(metadata={"plan_id": "p-123"})
    assert d.route == ROUTE_LOOP and d.source == "long_task"


# ---------------------------------------------------------------------------
# default: undecidable -> the general lane; KAIROS_ROUTE_DEFAULT=loop restores
#          the historical loop default
# ---------------------------------------------------------------------------

def _clear_route_default(monkeypatch):
    monkeypatch.delenv(ROUTE_DEFAULT_ENV, raising=False)


def test_default_route_is_skeleton_when_unset(monkeypatch):
    _clear_route_default(monkeypatch)
    assert default_route() == ROUTE_SKELETON


@pytest.mark.parametrize("value", ["skeleton", "SKELETON", " Skeleton ", "", "   ",
                                   "banana", "skel", "loopback"])
def test_default_route_is_skeleton_for_everything_but_the_exact_word(monkeypatch, value):
    """Only the exact word 'loop' changes the default back."""
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, value)
    assert default_route() == ROUTE_SKELETON


@pytest.mark.parametrize("value", ["loop", "LOOP", " loop "])
def test_default_route_is_loop_only_for_the_exact_word(monkeypatch, value):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, value)
    assert default_route() == ROUTE_LOOP


def test_undecided_default_is_the_general_lane_without_the_env(tmp_path, monkeypatch):
    """Baseline: with nothing set, an undecided task goes to the general lane."""
    _clear_route_default(monkeypatch)
    d = route_task(workspace=tmp_path)  # empty dir -> undecided
    assert d.route == ROUTE_SKELETON
    assert d.source == "default"
    assert d.workspace_kind == WS_REPO
    assert "general lane" in d.reason


def test_empty_workspace_is_undecided_and_defaults_to_the_general_lane(tmp_path):
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_SKELETON
    assert d.source == "default"
    assert d.workspace_kind == WS_REPO


def test_no_workspace_at_all_defaults_to_the_general_lane():
    d = route_task()
    assert d.route == ROUTE_SKELETON
    assert d.source == "default"


def test_a_missing_directory_defaults_to_the_general_lane(tmp_path):
    d = route_task(workspace=tmp_path / "does-not-exist")
    assert d.route == ROUTE_SKELETON
    assert d.signals["scanned"] is False


def test_workspace_with_only_unknown_files_is_undecided(tmp_path):
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    (tmp_path / "notes").write_text("no extension", encoding="utf-8")
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_SKELETON and d.source == "default"


# ---------------------------------------------------------------------------
# (test 6) the escape hatch: KAIROS_ROUTE_DEFAULT=loop restores the old default
# ---------------------------------------------------------------------------

def test_loop_default_keeps_undecided_on_the_loop(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "loop")
    d = route_task(workspace=tmp_path)  # empty dir -> undecided
    assert d.route == ROUTE_LOOP and d.source == "default"
    assert ROUTE_DEFAULT_ENV in d.reason


def test_loop_default_does_not_reroute_an_explicit_repo(tmp_path, monkeypatch):
    """An explicit code signal outranks the configured default."""
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "loop")
    _docs_only(tmp_path)  # would heuristically be docs
    d = route_task(explicit_kind="repo", workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "explicit"


def test_loop_default_does_not_reroute_an_explicit_docs(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "loop")
    d = route_task(explicit_kind="docs", workspace=tmp_path)
    assert d.route == ROUTE_SKELETON and d.source == "explicit"


def test_loop_default_leaves_the_heuristic_repo_on_the_loop(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "loop")
    _code_repo(tmp_path)
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_LOOP
    assert d.source == "heuristic"
    assert d.workspace_kind == WS_REPO


def test_loop_default_still_sends_a_docs_workspace_to_the_general_lane(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "loop")
    _docs_only(tmp_path)
    d = route_task(workspace=tmp_path)
    assert d.route == ROUTE_SKELETON and d.source == "heuristic"


def test_loop_default_does_not_reroute_a_coding_intent_message(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTE_DEFAULT_ENV, "loop")
    d = route_task(requirement="修复登录 bug", workspace=tmp_path)
    assert d.route == ROUTE_LOOP and d.source == "coding_intent"


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


def test_coding_intent_decision_is_loggable(tmp_path):
    """The route decision is never silent: route/source/reason are all present."""
    d = route_task(requirement="fix the bug", workspace=tmp_path)
    payload = d.to_dict()
    assert payload["route"] == ROUTE_LOOP
    assert payload["source"] == "coding_intent"
    assert payload["reason"]
    assert payload["signals"]["coding_intent"]


def test_large_workspace_scan_is_bounded(tmp_path):
    """The scan never walks an unbounded tree -- it stops at max_files."""
    s = scan_workspace(tmp_path, max_files=3)  # empty dir, just must not hang
    assert s["scanned"] is True
    for i in range(50):
        (tmp_path / f"f{i}.py").write_text("x=1\n", encoding="utf-8")
    s2 = scan_workspace(tmp_path, max_files=3)
    assert s2["total_files"] <= 3
