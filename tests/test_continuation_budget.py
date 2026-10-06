"""B 方案：有实质进展的续跑不计次；以及"宣布了下一步却停下"会被追责。

覆盖方式说明（诚实边界）：
- 纯函数部分（承诺标记的识别）行为级验证；
- 装配部分用源码级守卫（与 tests/test_r37_ui_source.py 同一约定），
  因为构造一个可脚本化的 LLM 桩需要动用那个测试文件里的私有 harness，
  跨文件复用比这里钉位置更脆。
"""
from pathlib import Path

from kairos.agents import base as B

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 1) 两个上限都存在，且生产性上限更大（有进展 = 更宽容）
# --------------------------------------------------------------------------

def test_productive_cap_is_larger_than_the_spin_cap():
    assert B.MAX_CHAT_CONTINUATIONS >= 1
    assert B.MAX_PRODUCTIVE_CONTINUATIONS > B.MAX_CHAT_CONTINUATIONS, (
        "a run that is producing real side effects must get more room than a "
        "run that is only investigating"
    )
    assert B.MAX_FOLLOW_THROUGHS >= 1


# --------------------------------------------------------------------------
# 2) 承诺标记：认第一人称的下一步，不认给用户的建议
# --------------------------------------------------------------------------

def test_first_person_promises_are_recognised():
    for text in ["先把原文取全，再据实纠正。",
                 "先看一下 ADR 列表，然后统一改。",
                 "接下来我会把 README 里的模块表改对。",
                 "I'll rewrite the module list next.",
                 "let me read the rest and then fix it"]:
        # 与实现一致：大小写不敏感（模型写 "I'll"，标记表是小写）
        assert any(m in text.lower() for m in B.FOLLOW_THROUGH_MARKERS), text


def test_second_person_suggestions_are_not_promises():
    for text in ["接下来你可以运行 npm run dev。",
                 "完成了，需要我继续吗？",
                 "All done. You can review the diff above."]:
        assert not any(m in text.lower() for m in B.FOLLOW_THROUGH_MARKERS), text


# --------------------------------------------------------------------------
# 3) 源码级守卫：装配必须真的在循环里
# --------------------------------------------------------------------------

def _code() -> str:
    src = (REPO_ROOT / "kairos/agents/base.py").read_text(encoding="utf-8")
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


def test_loop_grants_productive_continuations_without_spending_the_spin_budget():
    src = _code()
    assert "segment_made_progress = False" in src, "per-segment flag must be reset"
    assert "segment_made_progress = True" in src, "per-segment flag must be set"
    assert "productive_continuations += 1" in src
    assert "productive_continuations < MAX_PRODUCTIVE_CONTINUATIONS" in src
    # 生产性续跑必须排在"消耗式续跑"的判断之前，否则永远轮不到它
    i_productive = src.find("productive_continuations < MAX_PRODUCTIVE_CONTINUATIONS")
    i_spin = src.find("if continuations >= MAX_CHAT_CONTINUATIONS:")
    assert 0 < i_productive < i_spin, "productive branch must come first"


def test_a_promised_next_step_is_not_accepted_as_an_exit():
    src = _code()
    i = src.find("follow_nudge_active = True")
    assert i > 0, "the follow-through flag is never set"
    assert "FOLLOW_THROUGH_MARKERS" in src[:i + 400] or "FOLLOW_THROUGH_MARKERS" in src
    # 提示必须真的注入到请求里，并且排在语言锚点之前（锚点仍是最末）
    i_inject = src.find("content=FOLLOW_THROUGH_NUDGE")
    i_anchor = src.find("content=_lang_anchor")
    assert 0 < i_inject < i_anchor, (
        "the follow-through nudge must be injected into the request, before the "
        "language anchor so the anchor stays last"
    )
