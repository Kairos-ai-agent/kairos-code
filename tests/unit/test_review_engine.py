"""Tests for kairos.review.engine (regression: B-08 non-greedy regex)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from kairos.llm.base import LLMConfig, LLMResponse
from kairos.review.engine import ReviewEngine


def _make_engine_with_response(text: str) -> ReviewEngine:
    """Build a ReviewEngine whose LLM provider returns `text` from complete()."""
    engine = ReviewEngine(LLMConfig(provider="openai", model="gpt-4o"))
    fake_provider = MagicMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content=text, model="m", tool_calls=None)
    )
    engine._llm = fake_provider
    return engine


@pytest.mark.asyncio
async def test_fenced_json_block_parses(tmp_workspace):
    body = json.dumps([
        {"category": "CRITICAL", "description": "d1", "code_snippet": "x", "suggestion": "s"}
    ])
    engine = _make_engine_with_response(f"```json\n{body}\n```")
    fr = await engine.review_file("foo.py", "print('hi')")
    assert len(fr.issues) == 1
    assert fr.issues[0].category == "CRITICAL"
    assert fr.score == 80  # 100 - 20 per critical


@pytest.mark.asyncio
async def test_bare_json_array_parses(tmp_workspace):
    body = json.dumps([{"category": "MAJOR", "description": "d"}])
    engine = _make_engine_with_response(body)
    fr = await engine.review_file("foo.py", "print('hi')")
    assert len(fr.issues) == 1
    assert fr.issues[0].category == "MAJOR"


@pytest.mark.asyncio
async def test_unrelated_brackets_dont_confuse_parser(tmp_workspace):
    r"""The old regex `\[.*\]` would span from the first '[' to the last ']'.
    The bracket-aware regex must isolate the inner JSON array."""
    text = "noise [1, 2, 3] more noise " + json.dumps(
        [{"category": "MINOR", "description": "real"}]
    ) + " trailing [4, 5]"
    engine = _make_engine_with_response(text)
    fr = await engine.review_file("foo.py", "x")
    assert len(fr.issues) == 1
    assert fr.issues[0].description == "real"


@pytest.mark.asyncio
async def test_unparseable_response_yields_empty_review(tmp_workspace):
    engine = _make_engine_with_response("just plain text, no JSON here")
    fr = await engine.review_file("foo.py", "x")
    assert fr.issues == []
    assert fr.score == 100


@pytest.mark.asyncio
async def test_score_decreases_with_severity(tmp_workspace):
    issues = json.dumps([
        {"category": "CRITICAL"},
        {"category": "MAJOR"},
        {"category": "MINOR"},
        {"category": "MINOR"},
        {"category": "SUGGESTION"},  # doesn't affect score
    ])
    engine = _make_engine_with_response(issues)
    fr = await engine.review_file("foo.py", "x")
    # 100 - 20 (1 critical) - 10 (1 major) - 3*2 (2 minor) = 64
    assert fr.score == 64