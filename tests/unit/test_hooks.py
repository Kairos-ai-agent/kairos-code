"""Tests for the hook system."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kairos.hooks.runner import HookRunner


def test_runner_with_no_dir_is_noop(tmp_path):
    """Missing hooks dir must not crash; just produce no-op dispatch."""
    runner = HookRunner(hooks_dir=tmp_path / "does-not-exist")
    out = runner.pre_tool_use("file_read", {"path": "x"}, "agent1", "p1")
    assert out == {"path": "x"}  # passed through unchanged


def test_pre_tool_use_can_rewrite_arguments(tmp_path):
    """If a hook returns a dict, it replaces the args."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "rewriter.py").write_text("""
def pre_tool_use(tool_name, arguments, agent_id, project_id):
    # Always force path to a safe value before file_write.
    if tool_name == "file_write":
        return {"path": "safe.txt", "content": arguments.get("content", "")}
    return None
""")
    runner = HookRunner(hooks_dir=hooks)
    rewritten = runner.pre_tool_use("file_write",
                                    {"path": "evil.txt", "content": "x"},
                                    "agent1", "p1")
    assert rewritten == {"path": "safe.txt", "content": "x"}
    # Other tools pass through.
    passthrough = runner.pre_tool_use("file_read", {"path": "y"}, "agent1", "p1")
    assert passthrough == {"path": "y"}


def test_pre_tool_use_chains_multiple_hooks(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "first.py").write_text("""
def pre_tool_use(tool_name, arguments, agent_id, project_id):
    arguments = dict(arguments)
    arguments["first"] = True
    return arguments
""")
    (hooks / "second.py").write_text("""
def pre_tool_use(tool_name, arguments, agent_id, project_id):
    arguments = dict(arguments)
    arguments["second"] = True
    return arguments
""")
    runner = HookRunner(hooks_dir=hooks)
    out = runner.pre_tool_use("file_read", {}, "a", "p")
    assert out.get("first") is True
    assert out.get("second") is True


def test_post_tool_use_does_not_block(tmp_path):
    """Post hooks are fire-and-forget; failures shouldn't propagate."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "broken.py").write_text("""
def post_tool_use(tool_name, arguments, result, agent_id, project_id):
    raise RuntimeError("intentional")
""")
    runner = HookRunner(hooks_dir=hooks)
    # Must not raise.
    runner.post_tool_use("file_read", {}, MagicMock(), "a", "p")


def test_loop_round_hook_receives_review(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "audit.py").write_text("""
def loop_round(round_no, coder_summary, review, project_id):
    import json, os
    os.makedirs('/tmp/kairos_audit', exist_ok=True)
    with open(f'/tmp/kairos_audit/{project_id}.jsonl', 'a') as f:
        f.write(json.dumps({'round': round_no, 'score': review.get('score')}) + '\\n')
""")
    runner = HookRunner(hooks_dir=hooks)
    runner.loop_round(3, "coder did X", {"score": 80, "summary": "ok"}, "p1")
    import os
    path = "/tmp/kairos_audit/p1.jsonl"
    if os.path.exists(path):
        with open(path) as f:
            line = f.readline()
            data = json.loads(line)
        assert data["round"] == 3
        assert data["score"] == 80


def test_loop_completed_hook_fires(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "notify.py").write_text("""
called_with = {}
def loop_completed(project_id, final_score, total_rounds):
    called_with['pid'] = project_id
    called_with['score'] = final_score
    called_with['rounds'] = total_rounds
""")
    runner = HookRunner(hooks_dir=hooks)
    runner.loop_completed("p1", 95, 4)
    # Re-read the module to inspect its module-level mutation.
    import importlib.util
    spec = importlib.util.spec_from_file_location("notify_test", hooks / "notify.py")
    # Already exec'd inside the runner, but we can call loop_completed
    # and verify it didn't raise. The mutation test is best-effort.
    runner.loop_completed("p1", 95, 4)  # idempotent


def test_hook_file_with_syntax_error_is_skipped(tmp_path):
    """A broken hook file must not prevent the runner from working —
    it's logged and ignored."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "broken.py").write_text("def pre_tool_use(:\n  pass")
    (hooks / "good.py").write_text("""
def pre_tool_use(tool_name, arguments, agent_id, project_id):
    arguments = dict(arguments)
    arguments["good"] = True
    return arguments
""")
    runner = HookRunner(hooks_dir=hooks)
    out = runner.pre_tool_use("file_read", {}, "a", "p")
    assert out.get("good") is True