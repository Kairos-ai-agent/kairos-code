"""The work-and-report discipline reaches every prompt, and stays small.

The user's standing complaint was that the agent investigates instead of
working: it describes a plan and stops, answers from memory, calls an untested
guess 「实测」, and reports the plan rather than the result. The fix is a single
short directive, carried by the chat path (project and no-project) and by the
agent base that every loop role is built from.

Four things are pinned here, each of which can break on its own:

* every concrete rule the directive promises is actually present (keyword
  assertions, not a snapshot of the prose);
* it stays under a hard length cap, so it cannot grow into a manifesto;
* it composes with the existing ACT_DONT_ASK_DIRECTIVE without duplicating it
  or contradicting it -- both ride in the same prompt;
* it contains nothing the release secret-scanner refuses (a false positive
  here would make the build unpublishable).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from kairos.agents.agent_parts.chat import AgentChatMixin
from kairos.agents.agent_parts.discipline import WORK_DISCIPLINE_DIRECTIVE
from kairos.agents.base import KairosAgent
from kairos.agents.identity import KAIROS_IDENTITY
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Hard cap: the whole point is a few executable lines, not an essay. The
#: English originals this was distilled from run several times longer.
MAX_CHARS = 1500


# ---------------------------------------------------------------------------
# the directive itself
# ---------------------------------------------------------------------------


def test_every_promised_rule_is_present():
    """Each clause must be a concrete behaviour we can point at."""
    text = WORK_DISCIPLINE_DIRECTIVE
    # act now, do not stop after describing a plan
    assert "立刻做" in text
    assert "计划" in text and "不算回复" in text
    # the deliverable is a verifiable artefact, not a description
    assert "可验证的产物" in text and "文件" in text
    assert "等于没做" in text
    # conclusions need tool evidence; never call an untested guess 实测
    assert "工具证据" in text
    assert "实测" in text and "不凭记忆" in text
    # parallel independent calls
    assert "并行" in text
    # honest reporting: never fabricate a plausible-looking result
    assert "编造" in text and "假结果" in text
    # re-read after an external write
    assert "回读" in text
    # done == every acceptance point verified
    assert "验收点" in text
    # the report shape: did / real result / not done + why, no exaggeration
    assert "汇报格式" in text
    assert "真实结果" in text and "不夸大" in text and "没做什么" in text


def test_the_language_rule_is_pinned():
    """Answer in the user's language -- the bug was a Chinese question met with
    an all-English answer because no rule said which language to reply in."""
    text = WORK_DISCIPLINE_DIRECTIVE
    assert "用户使用的语言" in text
    assert "中文" in text and "英文" in text
    assert "系统提示词" in text
    # It sits in the prominent slot: the first rule of the block.
    first_rule = text.splitlines()[1]
    assert first_rule.startswith("- 用用户使用的语言回答"), first_rule


def test_directive_stays_short():
    """A manifesto ships to every turn of every session -- keep it tight."""
    assert 400 <= len(WORK_DISCIPLINE_DIRECTIVE) <= MAX_CHARS, (
        f"work-discipline directive is {len(WORK_DISCIPLINE_DIRECTIVE)} chars; "
        f"target wins at 600-1200, hard cap {MAX_CHARS}"
    )
    # It is prose with rules, not a wall: one bullet per behaviour.
    bullets = [ln for ln in WORK_DISCIPLINE_DIRECTIVE.splitlines()[1:] if ln.strip()]
    assert 5 <= len(bullets) <= 10, f"expected a handful of bullets, got {len(bullets)}"


# ---------------------------------------------------------------------------
# it reaches every prompt-assembly path
# ---------------------------------------------------------------------------


def test_chat_prompt_without_a_project_carries_it():
    class _Stub(AgentChatMixin):
        project_id = None

    prompt = _Stub()._build_chat_system_prompt()
    assert KAIROS_IDENTITY in prompt
    assert WORK_DISCIPLINE_DIRECTIVE in prompt


def test_chat_prompt_with_a_project_carries_it():
    class _Stub(AgentChatMixin):
        project_id = "p1"

    class _Orch:
        def get_project(self, project_id):
            return SimpleNamespace(name="P", description="d", work_dir="")

    stub = _Stub()
    stub._orchestrator = _Orch()
    prompt = stub._build_chat_system_prompt()
    # The project branch is a different return statement than the fallback.
    assert "project P" in prompt
    assert WORK_DISCIPLINE_DIRECTIVE in prompt


def test_every_agent_task_prompt_carries_it():
    """Loop roles (Coder, Reviewer, subagents) come through the base class."""
    cfg = LLMConfig(provider="openai", model="m", api_key="sk-test",
                    base_url="https://example.invalid/v1")
    agent = KairosAgent(agent_id="a1", name="coder", role="coder",
                        system_prompt="ROLE PROMPT", llm_config=cfg,
                        message_bus=MessageBus(), tools=[])
    assert agent.system_prompt.startswith(KAIROS_IDENTITY)
    assert WORK_DISCIPLINE_DIRECTIVE in agent.system_prompt
    assert "ROLE PROMPT" in agent.system_prompt


# ---------------------------------------------------------------------------
# it composes with the blocks already there
# ---------------------------------------------------------------------------


def test_it_appears_once_and_does_not_duplicate_act_dont_ask():
    class _Stub(AgentChatMixin):
        project_id = None

    prompt = _Stub()._build_chat_system_prompt()
    assert prompt.count(WORK_DISCIPLINE_DIRECTIVE) == 1

    # The two blocks must not restate each other: neither carries the other's
    # distinctive sentence.
    from kairos.agents.agent_parts.chat import ACT_DONT_ASK_DIRECTIVE

    assert "同一个问题不要问第二遍" in ACT_DONT_ASK_DIRECTIVE
    assert "同一个问题不要问第二遍" not in WORK_DISCIPLINE_DIRECTIVE
    assert "不凭记忆" in WORK_DISCIPLINE_DIRECTIVE
    assert "不凭记忆" not in ACT_DONT_ASK_DIRECTIVE
    # And both survive side by side in the assembled prompt.
    assert "同一个问题不要问第二遍" in prompt


def test_it_does_not_contradict_act_dont_ask():
    """ACT_DONT_ASK says: act, do not ask again. The discipline must agree."""
    text = WORK_DISCIPLINE_DIRECTIVE
    for forbidden in ("先问用户", "询问用户", "请用户确认", "等用户确认",
                      "问第二遍", "征求用户意见"):
        assert forbidden not in text, (
            f"the work-discipline block must not re-open the asking door: {forbidden!r}"
        )
    # It tells the agent to act, not to stop and hand back.
    assert "立刻做" in text
    assert "走不通时直接说卡在哪" in text


# ---------------------------------------------------------------------------
# the publish gate must stay green with this text in the tree
# ---------------------------------------------------------------------------

SCANNER = REPO_ROOT / "scripts" / "scan_binary_secrets.py"


def _scan(text: str, tmp_path: Path):
    spec = importlib.util.spec_from_file_location("scan_binary_secrets_wd", SCANNER)
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    art = tmp_path / "prompt.bin"
    art.write_bytes(text.encode("utf-8"))
    return scanner.scan(art, local_values=[])


def test_the_directive_does_not_trip_the_secret_scanner(tmp_path):
    """A credential-shaped string here would make every release unpublishable."""
    result = _scan(WORK_DISCIPLINE_DIRECTIVE, tmp_path)
    assert result["hits"] == [], result["hits"]


def test_the_whole_chat_prompt_does_not_trip_the_secret_scanner(tmp_path):
    class _Stub(AgentChatMixin):
        project_id = None

    result = _scan(_Stub()._build_chat_system_prompt(), tmp_path)
    assert result["hits"] == [], result["hits"]
