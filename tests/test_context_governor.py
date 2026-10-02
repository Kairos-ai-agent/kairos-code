"""Context governance: elide old tool bodies, shrink after a rejection.

Two behaviours keep a long session alive, and both are tested here because
both are pure functions with no provider involved:

* a tool result that has scrolled out of the recent window must stop costing
  tokens on *every* later request, while the session keeps the full text;
* a request the provider rejected as too large must be rebuilt strictly
  smaller — keeping the system prompts and the message being answered —
  instead of ending the run.
"""
from __future__ import annotations

from kairos.context_governor import (
    context_stats,
    elide_old_tool_results,
    shrink_for_overflow,
)
from kairos.llm.base import LLMMessage
from kairos.llm.errors import is_context_length_error


def _tool(body: str = "x" * 500, tool_id: str = "t1",
          name: str = "read_file") -> LLMMessage:
    return LLMMessage(role="tool", content=body, tool_call_id=tool_id,
                      name=name)


def _conversation(n_tools: int = 6, body: int = 500) -> list:
    msgs = [LLMMessage(role="system", content="sys")]
    for i in range(n_tools):
        msgs.append(LLMMessage(role="user", content=f"ask {i}"))
        msgs.append(LLMMessage(role="assistant", content="working"))
        msgs.append(_tool(body="x" * body, tool_id=f"t{i}", name=f"tool{i}"))
    return msgs


# ---------------------------------------------------------------------------
# elision
# ---------------------------------------------------------------------------


def test_old_tool_bodies_are_stubbed_out():
    msgs = _conversation(n_tools=6)
    out, report = elide_old_tool_results(msgs, keep_recent=4)

    assert report.elided == 2, "the two oldest tool results should be stubbed"
    assert report.chars_saved > 0
    tool_bodies = [m.content for m in out if m.role == "tool"]
    assert tool_bodies[0].startswith("[tool0 output elided")
    assert tool_bodies[1].startswith("[tool1 output elided")
    assert tool_bodies[2] == "x" * 500, "recent results stay verbatim"
    assert tool_bodies[-1] == "x" * 500


def test_elision_keeps_the_fields_the_provider_validates():
    """The stub replaces the body only — tool_call_id/name must survive, or
    the request stops being a valid tool reply."""
    out, _ = elide_old_tool_results(_conversation(n_tools=3), keep_recent=1)
    stubbed = [m for m in out if m.role == "tool"][0]
    assert stubbed.tool_call_id == "t0"
    assert stubbed.name == "tool0"


def test_elision_does_not_touch_the_stored_messages():
    """Session memory keeps every byte; only the outgoing copy is trimmed."""
    msgs = _conversation(n_tools=6)
    before = [m.content for m in msgs]
    elide_old_tool_results(msgs, keep_recent=1)
    assert [m.content for m in msgs] == before


def test_non_tool_messages_are_never_touched():
    msgs = _conversation(n_tools=4)
    out, _ = elide_old_tool_results(msgs, keep_recent=0)
    for original, new in zip(msgs, out):
        if original.role != "tool":
            assert new.content == original.content


def test_short_bodies_are_left_alone():
    """A stub that saves 30 characters is noise, not compression."""
    msgs = [LLMMessage(role="tool", content="tiny", tool_call_id="t",
                       name="t")]
    out, report = elide_old_tool_results(msgs, keep_recent=0)
    assert report.elided == 0
    assert out[0].content == "tiny"


def test_dict_messages_are_supported():
    """``/compact`` and friends pass plain dicts, not LLMMessage."""
    msgs = [{"role": "tool", "content": "y" * 400, "tool_call_id": "t"},
            {"role": "user", "content": "hi"}]
    out, report = elide_old_tool_results(msgs, keep_recent=0)
    assert report.elided == 1
    assert "elided" in out[0]["content"]
    assert out[1] == {"role": "user", "content": "hi"}


def test_keep_recent_zero_elides_every_tool_result():
    out, report = elide_old_tool_results(_conversation(n_tools=3),
                                         keep_recent=0)
    assert report.elided == 3


# ---------------------------------------------------------------------------
# shrink after an overflow rejection
# ---------------------------------------------------------------------------


def test_shrink_keeps_system_and_the_final_message():
    msgs = _conversation(n_tools=8)
    msgs.append(LLMMessage(role="user", content="the question being answered"))
    out, report = shrink_for_overflow(msgs, keep_recent_messages=6)

    assert len(out) < len(msgs)
    assert out[0].role == "system", "the role prompt must stay first"
    assert out[-1].content == "the question being answered"
    assert report.dropped > 0


def test_shrink_is_strictly_smaller_and_frees_tokens():
    msgs = _conversation(n_tools=10, body=4000)
    before = sum(len(m.content or "") for m in msgs)
    out, report = shrink_for_overflow(msgs)
    after = sum(len(m.content or "") for m in out)

    assert after < before
    assert report.chars_saved > 0


def test_shrink_never_returns_an_empty_request():
    msgs = [LLMMessage(role="system", content="sys"),
            LLMMessage(role="user", content="hi")]
    out, _ = shrink_for_overflow(msgs)
    assert out, "a short conversation must survive untouched"
    assert out[-1].content == "hi"


# ---------------------------------------------------------------------------
# overflow classification
# ---------------------------------------------------------------------------


def test_provider_context_length_messages_are_recognised():
    cases = [
        RuntimeError("400 - This model's maximum context length is 65536 "
                     "tokens. However, you requested 90000 tokens"),
        RuntimeError("invalid_request_error: prompt is too long: 210000 "
                     "tokens > 200000 maximum"),
        RuntimeError('{"error": {"code": "context_length_exceeded"}}'),
        RuntimeError("LLM call failed after 5 retries: context_length_exceeded"),
        RuntimeError("Please reduce the length of the messages"),
    ]
    for exc in cases:
        assert is_context_length_error(exc), exc


def test_http_413_counts_as_an_oversized_request():
    class _TooLargeError(Exception):
        status_code = 413

    assert is_context_length_error(_TooLargeError("payload"))


def test_ordinary_failures_are_not_mistaken_for_overflow():
    for exc in (RuntimeError("Connection reset by peer"),
                RuntimeError("401 Authentication Fails"),
                RuntimeError("read timeout after 60s"),
                RuntimeError("429 rate limit exceeded")):
        assert not is_context_length_error(exc), exc


def test_stats_report_where_the_context_goes():
    stats = context_stats(_conversation(n_tools=4, body=1000))
    assert stats["messages"] == 13          # 1 system + 4 * 3
    assert stats["tool_messages"] == 4
    assert stats["tool_chars"] == 4000
    assert stats["approx_tokens"] == stats["chars"] // 4
