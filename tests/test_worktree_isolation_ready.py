"""Fast checks for the two fixes: no empty-box worktrees, no repeat questions.

The orchestrator fixture in tests/test_integration.py hangs on this machine
(asyncio IOCP), so the guard is also pinned here at the unit level, where it can
actually run locally. The orchestrator-level assertions live in
tests/test_integration.py and are judged by CI.
"""
from __future__ import annotations

import subprocess

import pytest

from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig
from kairos.worktree import WorktreeManager


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True,
                   capture_output=True)


def _repo_with_commit(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "T")
    (path / "README.md").write_text("hi", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


def test_unborn_head_is_not_ready_for_isolation(tmp_path):
    """git init alone: a worktree would be created EMPTY."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    assert WorktreeManager(repo_path=tmp_path).is_ready_for_isolation() is False


def test_untracked_files_are_not_ready_for_isolation(tmp_path):
    """The user's real files must not be left behind in the checkout."""
    _repo_with_commit(tmp_path)
    (tmp_path / "wip.py").write_text("print(1)\n", encoding="utf-8")
    assert WorktreeManager(repo_path=tmp_path).is_ready_for_isolation() is False


def test_committed_clean_repo_is_ready_for_isolation(tmp_path):
    """The normal case still isolates, so the feature is not disabled."""
    _repo_with_commit(tmp_path)
    assert WorktreeManager(repo_path=tmp_path).is_ready_for_isolation() is True


def test_non_git_directory_is_rejected_at_construction(tmp_path):
    """A non-git work_dir never reaches the guard: the manager refuses to exist.

    That is the contract the orchestrator already relies on (it wraps the
    constructor in try/except and skips isolation), so pin it rather than
    pretending the instance method can be called here.
    """
    from kairos.worktree import WorktreeError

    with pytest.raises(WorktreeError):
        WorktreeManager(repo_path=tmp_path)


def test_chat_prompt_forbids_asking_the_same_question_twice():
    """The prompt must say the instruction is the authority and questions close."""
    cfg = LLMConfig(provider="openai", model="m", api_key="sk-test",
                    base_url="https://example.invalid/v1")
    agent = KairosAgent(agent_id="a1", name="coder", role="coder",
                        system_prompt="sys", llm_config=cfg,
                        message_bus=MessageBus(), tools=[])
    prompt = agent._build_chat_system_prompt()
    assert "同一个问题不要问第二遍" in prompt
    assert "用户的指令就是授权" in prompt
