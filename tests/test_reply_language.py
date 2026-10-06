"""回答语言：① 系统提示词末尾钉住 ② 每次请求的最末一条挂语言锚点（不落库）。

为什么要有这条测试：用户抱怨过两次「依然回复我英文」。第一次只把规则加进纪律块
（system prompt 中段），被最末尾的英文角色提示压过；第二次把锚点挂在用户消息上，
几轮英文工具输出之后就把它顶到很远的位置，模型又开始说英文。
最终机制：锚点**每次请求都追加在最末**（请求级，不进历史），这里钉住位置与判定函数。
"""
from pathlib import Path

import pytest

from kairos.agents.agent_parts.discipline import (
    LANGUAGE_DIRECTIVE,
    LANGUAGE_USER_ANCHOR,
    WORK_DISCIPLINE_DIRECTIVE,
    needs_language_anchor,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 1) 指令本身：双语、明确、覆盖习惯性英文
# --------------------------------------------------------------------------

def test_language_directive_says_same_language_as_the_user():
    assert "same language" in LANGUAGE_DIRECTIVE.lower()
    assert "中文" in LANGUAGE_DIRECTIVE
    assert "英文" in LANGUAGE_DIRECTIVE
    assert "总结" in LANGUAGE_DIRECTIVE and "思考" in LANGUAGE_DIRECTIVE


def test_user_anchor_is_explicit_about_chinese():
    assert "中文" in LANGUAGE_USER_ANCHOR
    assert LANGUAGE_USER_ANCHOR.strip()


def test_discipline_block_still_keeps_its_own_language_bullet():
    assert "用户使用的语言" in WORK_DISCIPLINE_DIRECTIVE


# --------------------------------------------------------------------------
# 2) 判定函数：见到汉字就挂锚点，英文消息不挂
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "帮我改一下这个 bug",
    "照 DEVELOPMENT_WORKFLOW.md §3 落地",
    "OK，你来动手",
    "为什么反复无法执行？为什么不支持bash命令？",
])
def test_chinese_messages_get_the_anchor(text):
    assert needs_language_anchor(text) is True


@pytest.mark.parametrize("text", ["", "fix this bug please", "run npm run build",
                                  "DEVELOPMENT_WORKFLOW.md"])
def test_non_chinese_messages_do_not_get_the_anchor(text):
    assert needs_language_anchor(text) is False


# --------------------------------------------------------------------------
# 3) 装配位置（源码级守卫，理由同 tests/test_r37_ui_source.py：
#    直接实例化 KairosAgent 需要配置与 provider，这里钉位置更稳）
# --------------------------------------------------------------------------

def _code(rel: str) -> str:
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    return "\n".join(line for line in src.splitlines() if not line.strip().startswith("#"))


def test_system_prompt_ends_with_the_language_directive():
    src = _code("kairos/agents/base.py")
    i = src.find("self.system_prompt = (")
    assert i > 0, "system_prompt assembly not found"
    segment = src[i:i + 700]
    assert "LANGUAGE_DIRECTIVE" in segment, (
        "LANGUAGE_DIRECTIVE must be appended to self.system_prompt itself"
    )
    assert segment.find("KAIROS_IDENTITY") < segment.find("WORK_DISCIPLINE_DIRECTIVE")
    assert segment.find("WORK_DISCIPLINE_DIRECTIVE") < segment.find("LANGUAGE_DIRECTIVE")


def test_anchor_is_re_stated_on_every_request_and_stays_request_scoped():
    """锚点必须每轮请求都追加在最末，而不是只挂在用户消息上。"""
    src = _code("kairos/agents/base.py")
    assert "needs_language_anchor(message)" in src, "anchor flag not computed"
    # 历史里的用户消息不能被改写（否则锚点会被后续工具输出埋掉）
    assert "content=_chat_content" not in src, "anchor must not be baked into the user message"
    assert 'user_msg = LLMMessage(role="user", content=message)' in src
    # 每次请求末尾追加，且排在无进展助推之后
    i = src.find("content=NO_PROGRESS_NUDGE)]")
    assert i > 0
    tail = src[i:i + 400]
    assert "_lang_anchor" in tail, "anchor must be appended inside the request loop"
    assert 'content=_lang_anchor' in tail, "anchor must ride as the newest user message"
