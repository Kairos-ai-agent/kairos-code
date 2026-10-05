"""The agent introduces itself as Kairos code, not as another vendor's model.

A model behind a relay may identify as anything it likes. The endpoint this
project points at answers "I am Claude, made by Anthropic" when asked, so the
identity is stated in the prompt instead of being left to the model's default.
(User, verbatim: 「改成我是Kairos code，Kairos开发的」.)
"""

from pathlib import Path

from kairos.agents.identity import KAIROS_IDENTITY

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_identity_states_kairos_and_no_other_vendor():
    assert "Kairos code" in KAIROS_IDENTITY
    assert "Kairos" in KAIROS_IDENTITY
    # The whole point: the model must not claim these on its own.
    for foreign in ("Anthropic", "Claude", "OpenAI", "Gemini", "Google", "Meta"):
        assert foreign not in KAIROS_IDENTITY, (
            f"the identity must not mention {foreign} — that is exactly what "
            "the model used to claim by itself"
        )


def test_chat_system_prompt_carries_the_identity():
    """The single-turn path is where the model speaks in the first person."""
    from kairos.agents.agent_parts.chat import AgentChatMixin

    class _Stub(AgentChatMixin):
        project_id = None

    prompt = _Stub()._build_chat_system_prompt()
    assert KAIROS_IDENTITY in prompt
    # The old fallback left the identity entirely to the model.
    assert "helpful assistant" not in prompt


def test_every_agent_prompt_is_prefixed_with_the_identity():
    """Loop roles (Coder, Reviewer, subagents) get it from the base class."""
    src = (REPO_ROOT / "kairos" / "agents" / "base.py").read_text(encoding="utf-8")
    assert "from kairos.agents.identity import KAIROS_IDENTITY" in src
    # It is prepended in __init__, so every role inherits it.
    assert 'f"{KAIROS_IDENTITY}' in src, (
        "base.py should prepend the identity to every agent's system prompt"
    )


def test_identity_survives_the_agents_md_merge(tmp_path):
    """A project AGENTS.md is merged *into* the prompt, not swapped for it.

    Most real projects carry an AGENTS.md; if the merge replaced the prompt
    instead of extending it, the identity would silently disappear there.
    """
    from kairos.agents_md import AgentsMdLoader

    (tmp_path / "AGENTS.md").write_text(
        "# Project rules\n\nBe terse.\n", encoding="utf-8"
    )
    loader = AgentsMdLoader(project_dir=str(tmp_path))
    merged = loader.merge_into_system_prompt(KAIROS_IDENTITY)
    assert KAIROS_IDENTITY in merged
