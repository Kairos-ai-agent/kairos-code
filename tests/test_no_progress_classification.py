"""无进展判定必须按【每一次调用】判，而不是按工具名判。

回归背景（用户第 3 次报同类问题）：`ls` / `cat` / `git status` 走 terminal 时，
旧判定把 terminal 当"能改状态"，于是 read_only_streak 每轮清零、助推从不触发，
模型把 40 步预算全烧在只读审计上，然后在"发现错误、宣布下一步"处停下。
"""
from pathlib import Path

from kairos.agents.base import (
    _call_makes_progress,
    _terminal_command_is_read_only,
    _tool_makes_progress,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 1) 命令级判定：只看不动的算调查
# --------------------------------------------------------------------------

def test_reading_commands_are_read_only():
    for cmd in ["ls -la", "ls", "cat README.md", "head -40 docs/adr/ADR-0001.md",
                "grep -rn TODO docs/", "find . -name '*.md'", "pwd",
                "git status --short", "git log --oneline -5", "git diff HEAD",
                "git branch -a", "wc -l README.md", "which python"]:
        assert _terminal_command_is_read_only(cmd) is True, cmd


def test_state_changing_commands_are_not_read_only():
    for cmd in ["mkdir -p apps/api", "echo hi > f.txt", "rm -rf build",
                "git commit -m x", "git add -A", "touch a.py",
                "python -m pytest -q", "npm run build", "cp a b",
                "cat x | tee y", ""]:
        assert _terminal_command_is_read_only(cmd) is False, cmd


# --------------------------------------------------------------------------
# 2) 调用级判定：terminal + 只读命令 = 调查；其余照旧
# --------------------------------------------------------------------------

def test_terminal_running_read_only_commands_is_investigation():
    assert _call_makes_progress("terminal", {"command": "ls -la"}) is False
    assert _call_makes_progress("terminal", {"command": "git status"}) is False
    assert _call_makes_progress("bash", {"command": "cat a.md"}) is False


def test_terminal_running_state_changing_commands_is_progress():
    assert _call_makes_progress("terminal", {"command": "mkdir -p apps/api"}) is True
    assert _call_makes_progress("terminal", {"command": "git commit -m x"}) is True


def test_terminal_without_command_and_unknown_tools_stay_conservative():
    # 拿不到命令文本时不猜：算进展（假助推比沉默更糟）
    assert _call_makes_progress("terminal", {}) is True
    assert _call_makes_progress("terminal", None) is True
    assert _call_makes_progress("some_new_tool", {}) is True


def test_read_and_write_tools_are_classified_as_before():
    assert _call_makes_progress("file_read", {"path": "a.md"}) is False
    assert _call_makes_progress("file_write", {"path": "a.md"}) is True
    assert _tool_makes_progress("file_read") is False
    assert _tool_makes_progress("file_write") is True


# --------------------------------------------------------------------------
# 3) 源码级守卫：聊天循环必须用调用级判定
# --------------------------------------------------------------------------

def test_chat_loop_uses_the_per_call_classifier():
    src = (REPO_ROOT / "kairos/agents/base.py").read_text(encoding="utf-8")
    assert '_call_makes_progress(tc.name' in src, (
        "the chat loop must judge progress per call (with arguments), "
        "otherwise terminal reads keep resetting the read-only streak"
    )
