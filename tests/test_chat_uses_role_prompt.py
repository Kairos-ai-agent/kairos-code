"""The UI chat path answers with the agent's real role prompt, not a chat persona.

The complaint was that the agent in the UI chat was a chatbot: it replied
conversationally instead of doing the work. The cause was concrete — the chat
path assembled its own prompt (identity + "Respond conversationally to the
user's message. Use tools when helpful.") and never read ``self.system_prompt``,
so the Coder role prompt (read before you write / smallest change / verify by
running the tests / cite your work) never reached a chat turn.

Four things are pinned here, each of which can break on its own:

* the Coder's own operating rules are actually in the chat prompt;
* the work-discipline block appears exactly once (the base class already
  injects it into ``self.system_prompt``, so a blind append would duplicate it);
* both branches (with and without a project) are non-empty and still open with
  the identity;
* the old conversational line is gone.
"""
from __future__ import annotations

from types import SimpleNamespace

from kairos.agents.agent_parts.chat import (
    ACT_DONT_ASK_DIRECTIVE,
    HOST_EXECUTION_ENVIRONMENT,
)
from kairos.agents.agent_parts.discipline import WORK_DISCIPLINE_DIRECTIVE
from kairos.agents.base import KairosAgent
from kairos.agents.identity import KAIROS_IDENTITY
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig

#: The old chat-only instruction. Its presence means the chat path went back to
#: being its own mini-prompt instead of using the role prompt.
OLD_CHAT_LINE = "Respond conversationally to the user's message."


def _coder_agent() -> KairosAgent:
    """A real agent with the Coder role prompt, as the UI chat path builds it."""
    from kairos.agents.roles.coder import SYSTEM_PROMPT as CODER_SYSTEM_PROMPT

    cfg = LLMConfig(provider="openai", model="m", api_key="sk-test",
                    base_url="https://example.invalid/v1")
    return KairosAgent(agent_id="a1", name="Coder", role="coder",
                       system_prompt=CODER_SYSTEM_PROMPT, llm_config=cfg,
                       message_bus=MessageBus(), tools=[])


class _Orch:
    def get_project(self, project_id):
        return SimpleNamespace(id=project_id, name="P", description="d",
                               work_dir="")


def _no_project_prompt() -> str:
    agent = _coder_agent()
    agent.project_id = None
    return agent._build_chat_system_prompt()


def _with_project_prompt() -> str:
    agent = _coder_agent()
    agent.project_id = "p1"
    agent._orchestrator = _Orch()
    return agent._build_chat_system_prompt()


# ---------------------------------------------------------------------------
# a) the role prompt is what the chat path answers with
# ---------------------------------------------------------------------------


def test_chat_prompt_carries_the_coder_role_rules():
    """The Coder's operating principles must survive into a chat turn."""
    prompt = _no_project_prompt()
    # The three clauses the loop turns rely on, verbatim from coder.py.
    assert "Read before you write." in prompt
    assert "Verify." in prompt
    assert "Cite your work." in prompt


def test_the_base_is_the_role_prompt_not_a_chat_persona():
    """``self.system_prompt`` (role prompt included) is the base."""
    agent = _coder_agent()
    agent.project_id = None
    prompt = agent._build_chat_system_prompt()
    assert agent.system_prompt in prompt
    assert agent.system_prompt.startswith(KAIROS_IDENTITY)


def test_project_branch_also_carries_the_role_rules():
    prompt = _with_project_prompt()
    assert "Read before you write." in prompt
    assert "Verify." in prompt
    assert "Cite your work." in prompt
    # ...and it is still the project-aware prompt.
    assert "project P" in prompt


# ---------------------------------------------------------------------------
# b) the discipline block appears exactly once (base.py already injects it)
# ---------------------------------------------------------------------------


def test_work_discipline_appears_exactly_once_without_a_project():
    prompt = _no_project_prompt()
    assert prompt.count(WORK_DISCIPLINE_DIRECTIVE) == 1


def test_work_discipline_appears_exactly_once_with_a_project():
    prompt = _with_project_prompt()
    assert prompt.count(WORK_DISCIPLINE_DIRECTIVE) == 1


def test_the_other_standing_rules_are_present_once_each():
    for prompt in (_no_project_prompt(), _with_project_prompt()):
        assert prompt.count(ACT_DONT_ASK_DIRECTIVE) == 1
        assert prompt.count(HOST_EXECUTION_ENVIRONMENT) == 1


# ---------------------------------------------------------------------------
# c) both branches are non-empty and open with the identity
# ---------------------------------------------------------------------------


def test_both_branches_are_non_empty_and_start_with_the_identity():
    for prompt in (_no_project_prompt(), _with_project_prompt()):
        assert prompt.strip()
        assert prompt.startswith(KAIROS_IDENTITY)


# ---------------------------------------------------------------------------
# d) the old conversational line is gone
# ---------------------------------------------------------------------------


def test_the_old_conversational_line_is_gone():
    for prompt in (_no_project_prompt(), _with_project_prompt()):
        assert OLD_CHAT_LINE not in prompt
        assert "Use tools when helpful." not in prompt
        assert "helpful assistant" not in prompt


def test_the_mixin_still_works_without_a_system_prompt():
    """A test stub built on the mixin alone must not blow up or go empty."""
    from kairos.agents.agent_parts.chat import AgentChatMixin

    class _Stub(AgentChatMixin):
        project_id = None

    prompt = _Stub()._build_chat_system_prompt()
    assert prompt.startswith(KAIROS_IDENTITY)
    assert prompt.count(WORK_DISCIPLINE_DIRECTIVE) == 1
    assert OLD_CHAT_LINE not in prompt
