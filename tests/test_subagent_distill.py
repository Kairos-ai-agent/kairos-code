"""G3: a sub-agent's parent receives a summary, not a raw transcript.

A child can spend tens of thousands of tokens exploring. Returning that
verbatim (previously ``result[:5000]``, a hard character cut) spends the
parent's remaining context on the child's working notes instead of on what
the child concluded — and a cut is not a summary: it keeps whatever happened
to be in the first 5000 characters.

What the parent carries is now a distillation; what it can *go and get* is
the full report, on disk, redacted.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kairos.config.settings import settings as ksettings
from kairos.llm.base import LLMResponse
from kairos.tools.subagent import _DISTILL_FALLBACK_CHARS, SubagentTool

SUMMARY = "## Findings\n- the switch lives in access_control.py:12\n- still open: retry policy"


class _StubLLM:
    def __init__(self, content: str = SUMMARY, error: Exception = None):
        self._content = content
        self._error = error
        self.prompts = []

    async def complete(self, messages, tools=None, **kw):
        self.prompts.append(messages[0].content)
        if self._error is not None:
            raise self._error
        return LLMResponse(content=self._content, model="m",
                           finish_reason="stop", usage={})

    async def close(self):
        pass


@pytest.fixture
def tool(tmp_path, monkeypatch):
    """A SubagentTool whose report directory is inside tmp_path."""
    monkeypatch.setattr(ksettings, "data_dir", tmp_path)
    parent = SimpleNamespace(_llm=_StubLLM(), _llm_timeout_s=5.0)
    t = SubagentTool()
    t.parent_agent = parent
    t.project_id = "p1"
    return t


# ---------------------------------------------------------------------------
# distillation
# ---------------------------------------------------------------------------


def test_long_reports_are_distilled(tool):
    result = "exploration log\n" * 400          # ~6.4k chars

    text, summarized = asyncio.run(tool._distill_result("find the switch", result))

    assert summarized is True
    assert text == SUMMARY
    assert len(text) < len(result)
    assert "find the switch" in tool.parent_agent._llm.prompts[0]


def test_short_reports_are_passed_through_untouched(tool):
    result = "short answer"

    text, summarized = asyncio.run(tool._distill_result("task", result))

    assert text == result
    assert summarized is False
    assert tool.parent_agent._llm.prompts == [], \
        "a summarisation round-trip that saves nothing is pure latency"


def test_distillation_failure_falls_back_to_a_bounded_cut(tool):
    tool.parent_agent._llm = _StubLLM(error=RuntimeError("provider down"))
    result = "y" * 9000

    text, summarized = asyncio.run(tool._distill_result("task", result))

    assert summarized is False
    assert len(text) == _DISTILL_FALLBACK_CHARS


def test_oversized_reports_are_truncated_for_the_summariser(tool):
    result = "z" * 60000

    asyncio.run(tool._distill_result("task", result))

    prompt = tool.parent_agent._llm.prompts[0]
    assert len(prompt) < 30000, "the digest input itself must fit a window"
    assert "chars omitted" in prompt


# ---------------------------------------------------------------------------
# the full report, on disk
# ---------------------------------------------------------------------------


def test_raw_report_is_persisted_and_redacted(tool, tmp_path):
    secret = "sk-" + "a" * 40
    path = tool._persist_raw_output("child1", "find the switch",
                                    f"found key {secret} in config")

    assert path is not None
    body = (tmp_path / "subagent_outputs" / "child1.md").read_text(encoding="utf-8")
    assert secret not in body, "a new on-disk copy of a key is not acceptable"
    assert "sk-<redacted>" in body
    assert "find the switch" in body


def test_persist_failure_is_not_fatal(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ksettings, "data_dir", blocked)
    t = SubagentTool()
    t.parent_agent = SimpleNamespace(_llm=_StubLLM(), _llm_timeout_s=5.0)

    assert t._persist_raw_output("c1", "task", "body") is None
