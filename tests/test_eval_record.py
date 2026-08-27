"""Tests for kairos.eval record / replay / derive (Round 13)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from kairos.eval import (
    CaseResult,
    SuiteResult,
    dataset_to_suite_spec,
    derive_and_write_suite,
    derive_from_git_log,
    load_dataset,
    record_run,
    replay_dataset,
)


def _make_suite(cases: List[CaseResult], name: str = "demo") -> SuiteResult:
    return SuiteResult(
        suite_name=name, run_id="r1", cases=cases,
        started_at=100.0, finished_at=200.0,
    )


def _make_case(name: str, passed: bool, score: float = 0.5,
               output: str = "out") -> CaseResult:
    return CaseResult(
        name=name, score=score, passed=passed,
        duration_ms=10, cost_usd=0.01, tokens_in=5, tokens_out=10,
        details={"output": output, "contains": {"hits": 1, "total": 1}},
    )


# ---------------------------------------------------------------------------
# record_run
# ---------------------------------------------------------------------------


def test_record_run_appends_passed_only(tmp_path: Path):
    suite = _make_suite([
        _make_case("a", passed=True, output="hello"),
        _make_case("b", passed=False, output="bye"),
    ])
    ds = tmp_path / "ds.jsonl"
    n = record_run(suite, ds, only_passed=True)
    assert n == 1
    entries = load_dataset(ds)
    assert len(entries) == 1
    assert entries[0]["case_name"] == "a"
    assert entries[0]["output"] == "hello"


def test_record_run_with_all_flag_keeps_everything(tmp_path: Path):
    suite = _make_suite([
        _make_case("a", passed=True),
        _make_case("b", passed=False),
    ])
    ds = tmp_path / "ds.jsonl"
    n = record_run(suite, ds, only_passed=False)
    assert n == 2


def test_record_run_is_append_only(tmp_path: Path):
    """Two record calls → two chunks in the dataset."""
    ds = tmp_path / "ds.jsonl"
    record_run(_make_suite([_make_case("a", True)]), ds)
    record_run(_make_suite([_make_case("b", True)]), ds)
    entries = load_dataset(ds)
    assert [e["case_name"] for e in entries] == ["a", "b"]


def test_record_run_creates_parent_dirs(tmp_path: Path):
    ds = tmp_path / "nested" / "dir" / "ds.jsonl"
    n = record_run(_make_suite([_make_case("a", True)]), ds)
    assert n == 1
    assert ds.exists()


def test_load_dataset_skips_corrupt_lines(tmp_path: Path):
    """A corrupt line in the JSONL is logged-and-skipped, not fatal."""
    ds = tmp_path / "ds.jsonl"
    good = json.dumps({"case_name": "good", "score": 1.0}) + "\n"
    ds.write_text(good + "{not json\n", encoding="utf-8")
    entries = load_dataset(ds)
    assert len(entries) == 1
    assert entries[0]["case_name"] == "good"


# ---------------------------------------------------------------------------
# dataset_to_suite_spec
# ---------------------------------------------------------------------------


def test_dataset_to_suite_spec_emits_contains_grader():
    entries = [
        {"case_name": "c1", "output": "hello world", "input": "p1"},
    ]
    spec = dataset_to_suite_spec(entries)
    assert spec["name"] == "replay"
    assert len(spec["cases"]) == 1
    assert spec["cases"][0]["name"] == "c1"
    graders = spec["cases"][0]["graders"]
    assert "contains" in graders[0]
    assert "hello world" in graders[0]["contains"][0]


def test_dataset_to_suite_spec_truncates_output_to_200():
    """Long outputs are truncated to 200 chars in the contains needle."""
    long = "x" * 500
    entries = [{"case_name": "c", "output": long}]
    spec = dataset_to_suite_spec(entries)
    assert len(spec["cases"][0]["graders"][0]["contains"][0]) == 200


# ---------------------------------------------------------------------------
# replay_dataset
# ---------------------------------------------------------------------------


def test_replay_dataset_runs_all_recorded_cases(tmp_path: Path):
    suite = _make_suite([
        _make_case("a", passed=True, output="aaa"),
        _make_case("b", passed=True, output="bbb"),
    ])
    ds = tmp_path / "ds.jsonl"
    record_run(suite, ds)
    # Stub target returns a different output for each input
    out_map = {"a": "different", "b": "bbb"}
    def target(prompt: str) -> Dict[str, Any]:
        for k, v in out_map.items():
            if k in prompt or k == "a":  # always returns a or b's expected
                return {"output": v, "tools_called": [],
                        "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                        "duration_ms": 1}
        return {"output": "x", "tools_called": [], "tokens_in": 0,
                "tokens_out": 0, "cost_usd": 0.0, "duration_ms": 1}
    # The stub target doesn't actually use the input — both cases
    # fail the contains check (their recorded output is no longer
    # produced). This is the correct "regression detected" behavior.
    res = replay_dataset(ds, target=target)
    assert len(res.cases) == 2


# ---------------------------------------------------------------------------
# derive_from_git_log
# ---------------------------------------------------------------------------


def test_derive_from_git_log_no_commits(tmp_path: Path):
    """An empty (or non-git) repo returns no cases."""
    # tmp_path is just a directory; git is unlikely to be a repo.
    # If it IS, we still get 0 because there are no kairos commits.
    cases = derive_from_git_log(tmp_path, limit=10)
    # Should not raise; may return [] for any of these reasons
    assert isinstance(cases, list)


def test_derive_from_kairos_commits(tmp_path: Path):
    """A repo with kairos-style commits produces cases from them."""
    # Initialize a temp git repo with a kairos-style commit
    home = tmp_path / "home"
    home.mkdir()
    os_env = {"HOME": str(home), "USERPROFILE": str(home),
              "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.c",
              "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.c"}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=os_env)
    subprocess.run(["git", "config", "user.email", "t@t.c"],
                   cwd=tmp_path, check=True, env=os_env)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=tmp_path, check=True, env=os_env)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, env=os_env)
    subprocess.run(
        ["git", "commit", "-q", "-m",
         "kairos: round 3 approved (score 85)\n\n"
         "## some summary\n\n"
         "# Plan at this round\n\n"
         "- [x] Read README\n"
         "- [>] Add CSV reader\n"
         "- [ ] Run tests\n"],
        cwd=tmp_path, check=True, env=os_env,
    )
    cases = derive_from_git_log(tmp_path, limit=10)
    assert len(cases) == 1
    c = cases[0]
    assert c["name"].startswith("git-round-3-score-85")
    needles = c["graders"][0]["contains"]
    assert "Read README" in needles
    assert "Add CSV reader" in needles
    assert "Run tests" in needles
    # The active-form "_..." suffix (if any) is stripped
    for n in needles:
        assert not n.endswith("_")


def test_derive_skips_non_kairos_commits(tmp_path: Path):
    """A non-kairos commit (different subject) is ignored."""
    home = tmp_path / "home"
    home.mkdir()
    os_env = {"HOME": str(home), "USERPROFILE": str(home),
              "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.c",
              "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.c"}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=os_env)
    subprocess.run(["git", "config", "user.email", "t@t.c"],
                   cwd=tmp_path, check=True, env=os_env)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=tmp_path, check=True, env=os_env)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, env=os_env)
    # Plain "Initial commit" — not a kairos commit
    subprocess.run(["git", "commit", "-q", "-m", "Initial commit"],
                   cwd=tmp_path, check=True, env=os_env)
    cases = derive_from_git_log(tmp_path, limit=10)
    assert cases == []


def test_derive_skips_commits_without_plan_block(tmp_path: Path):
    """A kairos commit with no plan block (older format) is ignored."""
    home = tmp_path / "home"
    home.mkdir()
    os_env = {"HOME": str(home), "USERPROFILE": str(home),
              "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.c",
              "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.c"}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=os_env)
    subprocess.run(["git", "config", "user.email", "t@t.c"],
                   cwd=tmp_path, check=True, env=os_env)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=tmp_path, check=True, env=os_env)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, env=os_env)
    # Kairos commit subject but no plan block
    subprocess.run(
        ["git", "commit", "-q", "-m",
         "kairos: round 1 approved (score 70)\n\njust a summary"],
        cwd=tmp_path, check=True, env=os_env,
    )
    cases = derive_from_git_log(tmp_path, limit=10)
    assert cases == []


# ---------------------------------------------------------------------------
# derive_and_write_suite
# ---------------------------------------------------------------------------


def test_derive_and_write_suite_writes_yaml(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    os_env = {"HOME": str(home), "USERPROFILE": str(home),
              "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.c",
              "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.c"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=os_env)
    subprocess.run(["git", "config", "user.email", "t@t.c"],
                   cwd=repo, check=True, env=os_env)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=repo, check=True, env=os_env)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=os_env)
    subprocess.run(
        ["git", "commit", "-q", "-m",
         "kairos: round 2 approved (score 90)\n\n# Plan at this round\n\n"
         "- [x] Initialize\n- [ ] Deploy\n"],
        cwd=repo, check=True, env=os_env,
    )
    out = tmp_path / "out.yaml"
    n = derive_and_write_suite(repo, out, name="from-tests")
    assert n == 1
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "name: from-tests" in text
    assert "git-round-2-score-90" in text
    assert "Initialize" in text
    assert "Deploy" in text


def test_derive_and_write_suite_no_commits_writes_empty(tmp_path: Path):
    out = tmp_path / "out.yaml"
    n = derive_and_write_suite(tmp_path, out, name="empty")
    assert n == 0
    assert out.exists()
    assert "cases: []" in out.read_text(encoding="utf-8")
