"""Tests for ResilientProvider, ToolCache, precheck helpers, and
cross-loop pattern detection. These cover the four big pieces added
in the 'super-agent + auto-review' upgrade."""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest


# ---------------------------------------------------------------- ResilientProvider

class _FakeResponse:
    def __init__(self, content="ok", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeProvider:
    """Minimal BaseLLMProvider-like stub."""
    def __init__(self, name="primary", responses=None, errors=None):
        self.name = name
        self.config = MagicMock()
        self.responses = list(responses or [_FakeResponse()])
        self.errors = list(errors or [])
        self.calls = 0

    async def complete(self, messages, tools=None, temperature=None, max_tokens=None):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.responses.pop(0) if self.responses else _FakeResponse()

    async def stream(self, messages, tools=None, temperature=None, max_tokens=None):
        resp = await self.complete(messages, tools, temperature, max_tokens)
        for chunk in [resp.content]:
            yield chunk

    async def close(self):
        pass


def test_resilient_provider_succeeds_on_first_try():
    from kairos.llm.resilient import ResilientProvider
    primary = _FakeProvider(responses=[_FakeResponse("hello")])
    rp = ResilientProvider(primary, max_retries=3, initial_backoff_s=0.01)
    resp = asyncio.run(rp.complete([]))
    assert resp.content == "hello"
    assert primary.calls == 1
    assert rp._consecutive_failures == 0


def test_resilient_provider_retries_on_rate_limit():
    """429 (RateLimitError-shaped exception) should be retried, with
    exponential backoff. We patch the sleep to keep the test fast."""
    from kairos.llm.resilient import ResilientProvider

    # Fake RateLimitError (just an exception class with that name)
    class RateLimitError(Exception):
        pass

    primary = _FakeProvider(
        errors=[RateLimitError("429"),
                RateLimitError("429"),
                RateLimitError("429")],
        responses=[_FakeResponse("recovered")],
    )
    rp = ResilientProvider(primary, max_retries=5,
                            initial_backoff_s=0.001, max_backoff_s=0.01,
                            failover_after=10)
    # Monkey-patch sleep so the test doesn't actually wait.
    rp_sleeps = []
    async def fake_sleep(s):
        rp_sleeps.append(s)
    asyncio.sleep = fake_sleep

    resp = asyncio.run(rp.complete([]))
    assert resp.content == "recovered"
    assert primary.calls == 4  # 3 fails + 1 success
    assert len(rp_sleeps) == 3  # 3 backoffs
    # Backoff should be increasing (exponential)
    assert rp_sleeps[0] < rp_sleeps[1] < rp_sleeps[2]


def test_resilient_provider_does_not_retry_on_4xx():
    """Non-retryable errors (e.g. bad request) should raise immediately."""
    from kairos.llm.resilient import ResilientProvider

    class BadRequestError(Exception):
        pass

    primary = _FakeProvider(errors=[BadRequestError("400 bad request")])
    rp = ResilientProvider(primary, max_retries=5, initial_backoff_s=0.01)

    with pytest.raises(BadRequestError):
        asyncio.run(rp.complete([]))
    assert primary.calls == 1  # no retry


def test_resilient_provider_failover_after_consecutive_failures():
    """If primary keeps failing past failover_after, switch to failover
    provider and let it succeed."""
    from kairos.llm.resilient import ResilientProvider

    class RateLimitError(Exception):
        pass

    primary = _FakeProvider(
        errors=[RateLimitError("429")] * 10,
    )
    failover = _FakeProvider(responses=[_FakeResponse("from_failover")])
    rp = ResilientProvider(primary, failover=failover, max_retries=4,
                            initial_backoff_s=0.001, max_backoff_s=0.01,
                            failover_after=2)

    async def fake_sleep(s):
        pass
    asyncio.sleep = fake_sleep

    resp = asyncio.run(rp.complete([]))
    assert resp.content == "from_failover"
    # Primary should have been called at most max_retries times before
    # failover kicks in.
    assert primary.calls >= 2
    assert failover.calls >= 1


def test_resilient_provider_drops_back_to_primary_after_success():
    """After a successful call (even via failover), subsequent calls
    should use the primary again — failover is for emergencies only."""
    from kairos.llm.resilient import ResilientProvider

    class RateLimitError(Exception):
        pass

    primary = _FakeProvider(
        errors=[RateLimitError("429"), RateLimitError("429")],
        responses=[_FakeResponse("primary_ok")],
    )
    failover = _FakeProvider(responses=[_FakeResponse("failover_ok")])
    rp = ResilientProvider(primary, failover=failover, max_retries=5,
                            initial_backoff_s=0.001,
                            failover_after=2)

    async def fake_sleep(s):
        pass
    asyncio.sleep = fake_sleep

    # First call: primary fails, failover kicks in
    r1 = asyncio.run(rp.complete([]))
    assert r1.content == "failover_ok"
    # After success, _using_failover is reset
    assert rp._using_failover is False
    # Second call: primary should be used directly
    r2 = asyncio.run(rp.complete([]))
    assert r2.content == "primary_ok"


def test_resilient_provider_stream_yields_content():
    from kairos.llm.resilient import ResilientProvider
    primary = _FakeProvider(responses=[_FakeResponse("streamed")])
    rp = ResilientProvider(primary)
    chunks = []
    async def collect():
        async for c in rp.stream([]):
            chunks.append(c)
    asyncio.run(collect())
    assert "streamed" in "".join(chunks)


# ---------------------------------------------------------------- ToolCache

def test_tool_cache_basic_get_set():
    from kairos.tools.cache import ToolCache, make_key
    c = ToolCache()
    k = make_key("file_read", path="a.py")
    assert c.get(k) is None  # miss
    c.set(k, "content")
    assert c.get(k) == "content"  # hit
    assert c.hits == 1
    assert c.misses == 1
    s = c.stats()
    assert s["size"] == 1
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert 0.0 < s["hit_rate"] <= 1.0


def test_tool_cache_clear_round_resets():
    from kairos.tools.cache import ToolCache, make_key
    c = ToolCache()
    c.set(make_key("x", path="a"), "1")
    c.set(make_key("x", path="b"), "2")
    c.get(make_key("x", path="a"))
    c.get(make_key("x", path="a"))
    assert c.hits == 2
    assert c.misses == 1
    c.clear()
    assert c.hits == 0
    assert c.misses == 0
    assert c.get(make_key("x", path="a")) is None


def test_make_key_drops_private_kwargs():
    from kairos.tools.cache import make_key
    k1 = make_key("x", path="a", _cache=object())
    k2 = make_key("x", path="a")
    assert k1 == k2


def test_make_key_distinguishes_args():
    from kairos.tools.cache import make_key
    k1 = make_key("file_read", path="a.py")
    k2 = make_key("file_read", path="b.py")
    k3 = make_key("terminal", path="a.py")
    assert k1 != k2
    assert k1 != k3
    assert k2 != k3


def test_get_cache_singleton():
    from kairos.tools import cache as cache_mod
    cache_mod.set_cache(None)  # start clean
    a = cache_mod.get_cache()
    b = cache_mod.get_cache()
    assert a is b  # module-level singleton


def test_clear_round_clears_singleton():
    from kairos.tools import cache as cache_mod
    cache_mod.set_cache(None)
    c = cache_mod.get_cache()
    c.set(("t", frozenset()), "v")
    cache_mod.clear_round()
    assert c.get(("t", frozenset())) is None


# ---------------------------------------------------------------- precheck

def test_extract_known_fixes_module_not_found():
    from kairos.loop.precheck import extract_known_fixes
    stderr = "ModuleNotFoundError: No module named 'pandas'"
    fixes = extract_known_fixes(stderr)
    assert len(fixes) >= 1
    assert any(f["kind"] == "missing_module" for f in fixes)
    assert "pip install" in fixes[0]["fix_instruction"]


def test_extract_known_fixes_port_in_use():
    from kairos.loop.precheck import extract_known_fixes
    stderr = "OSError: [Errno 98] Address already in use: 8000"
    fixes = extract_known_fixes(stderr)
    assert any(f["kind"] == "port_in_use" for f in fixes)


def test_extract_known_fixes_syntax_error():
    from kairos.loop.precheck import extract_known_fixes
    stderr = "SyntaxError: invalid syntax (test.py, line 12)"
    fixes = extract_known_fixes(stderr)
    assert any(f["kind"] == "syntax_error" for f in fixes)


def test_extract_known_fixes_empty_input():
    from kairos.loop.precheck import extract_known_fixes
    assert extract_known_fixes("") == []
    assert extract_known_fixes("just a normal log line") == []


def test_extract_known_fixes_caps_at_five():
    from kairos.loop.precheck import extract_known_fixes
    # 10 ModuleNotFoundError lines should be capped to 5 fixes
    stderr = "\n".join(f"ModuleNotFoundError: No module named 'm{i}'"
                        for i in range(10))
    fixes = extract_known_fixes(stderr)
    assert len(fixes) == 5


def test_format_self_debug_hint_empty():
    from kairos.loop.precheck import format_self_debug_hint
    assert format_self_debug_hint([]) == ""


def test_format_self_debug_hint_renders():
    from kairos.loop.precheck import format_self_debug_hint
    fixes = [
        {"kind": "missing_module", "match": "No module named 'x'",
         "fix_instruction": "pip install x"},
    ]
    out = format_self_debug_hint(fixes)
    assert "missing_module" in out
    assert "pip install x" in out


def test_format_precheck_for_prompt_empty_when_no_failures():
    from kairos.loop.precheck import format_precheck_for_prompt
    assert format_precheck_for_prompt({"has_failures": False}) == ""


def test_format_precheck_for_prompt_includes_lint():
    from kairos.loop.precheck import format_precheck_for_prompt
    precheck = {
        "has_failures": True,
        "lint": {"ok": False, "stdout": "E501 line too long", "stderr": ""},
        "tests": None,
    }
    out = format_precheck_for_prompt(precheck)
    assert "PRECHECK FAILURES" in out
    assert "E501" in out


def test_pre_check_workspace_no_workspace(tmp_path: Path):
    """If the workspace doesn't exist, return a 'workspace not found'
    marker so the loop can keep going."""
    from kairos.loop.precheck import pre_check_workspace
    missing = tmp_path / "does_not_exist"
    result = asyncio.run(pre_check_workspace(missing, []))
    assert "workspace not found" in result["summary"]


def test_pre_check_workspace_detects_pyproject(tmp_path: Path):
    """If a pyproject.toml exists and pytest is installed, the test
    runner should be pytest -q."""
    from kairos.loop.precheck import _auto_detect_test_command
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]")
    cmd = _auto_detect_test_command(tmp_path)
    assert cmd[0] == "pytest"


def test_pre_check_workspace_detects_package_json(tmp_path: Path):
    from kairos.loop.precheck import _auto_detect_test_command
    (tmp_path / "package.json").write_text("{}")
    cmd = _auto_detect_test_command(tmp_path)
    assert cmd[0] == "npm"


def test_pre_check_workspace_no_test_config(tmp_path: Path):
    from kairos.loop.precheck import _auto_detect_test_command
    cmd = _auto_detect_test_command(tmp_path)
    assert cmd is None


# ---------------------------------------------------------------- cross-loop patterns

def _make_round(category=None, severity="MAJOR", score=60, file=None,
                description="issue", failure_mode=None, approve=False):
    """Build a fake loop_rounds row with valid review_json."""
    issues = []
    if category:
        issues.append({
            "category": category,
            "severity": severity,
            "file": file or "f.py",
            "line": 1,
            "description": description,
            "fix_instruction": "fix it",
        })
    review = {
        "approve": approve,
        "score": score,
        "issues": issues,
        "summary": f"score={score}",
    }
    if failure_mode:
        review["_failure_mode"] = failure_mode
    return {
        "round": 1,
        "score": score,
        "approve": 1 if approve else 0,
        "review_summary": f"R score={score}",
        "review_json": json.dumps(review),
    }


def test_cross_loop_empty():
    from kairos.loop.review_loop import _detect_cross_loop_patterns
    assert _detect_cross_loop_patterns([]) == ""


def test_cross_loop_too_few_rounds():
    from kairos.loop.review_loop import _detect_cross_loop_patterns
    rounds = [_make_round(category="correctness", score=50)] * 2
    assert _detect_cross_loop_patterns(rounds) == ""


def test_cross_loop_repeated_category():
    """3+ consecutive rounds with 'correctness' as the dominant
    category should produce a streak advisory."""
    from kairos.loop.review_loop import _detect_cross_loop_patterns
    rounds = [
        _make_round(category="correctness", score=50),
        _make_round(category="correctness", score=55),
        _make_round(category="correctness", score=53),
    ]
    out = _detect_cross_loop_patterns(rounds)
    assert "correctness" in out
    assert "consecutive rounds" in out


def test_cross_loop_score_flatline():
    """3 rounds with similar low scores should produce a flatline
    advisory."""
    from kairos.loop.review_loop import _detect_cross_loop_patterns
    rounds = [
        _make_round(category="design", score=50),
        _make_round(category="design", score=51),
        _make_round(category="design", score=50),
    ]
    out = _detect_cross_loop_patterns(rounds)
    assert "flatlined" in out
    assert "50" in out


def test_cross_loop_hot_files():
    """Same file flagged in 3+ rounds should be surfaced."""
    from kairos.loop.review_loop import _detect_cross_loop_patterns
    rounds = [
        _make_round(category="correctness", score=60, file="hot.py"),
        _make_round(category="correctness", score=60, file="hot.py"),
        _make_round(category="correctness", score=60, file="hot.py"),
    ]
    out = _detect_cross_loop_patterns(rounds)
    assert "hot.py" in out


def test_cross_loop_all_infra():
    """If all 3 recent rounds are infra failures (not real review
    issues), surface an 'infrastructure layer' advisory so the Coder
    does not make speculative code changes."""
    from kairos.loop.review_loop import _detect_cross_loop_patterns
    rounds = [
        _make_round(category=None, score=0, failure_mode="infra_fail"),
        _make_round(category=None, score=0, failure_mode="parse_fail"),
        _make_round(category=None, score=0, failure_mode="tool_limit"),
    ]
    out = _detect_cross_loop_patterns(rounds)
    assert "infrastructure" in out.lower()


def test_cross_loop_clean_rounds_no_advisory():
    """If 3 rounds are mixed/healthy, no advisory."""
    from kairos.loop.review_loop import _detect_cross_loop_patterns
    rounds = [
        _make_round(category="design", score=80),
        _make_round(category="style", score=85),
        _make_round(category="security", score=90),
    ]
    out = _detect_cross_loop_patterns(rounds)
    assert out == ""


def test_cross_loop_handles_corrupt_json():
    """A row with unparseable review_json should not crash; we just
    skip it. If we end up with fewer than 3 parsed rounds, we return
    '' rather than guessing."""
    from kairos.loop.review_loop import _detect_cross_loop_patterns
    rounds = [
        _make_round(category="correctness", score=50),
        {"round": 2, "score": 50, "review_json": "not-json"},
        _make_round(category="correctness", score=50),
    ]
    # Only 2 parseable rounds - should return ""
    out = _detect_cross_loop_patterns(rounds)
    assert out == ""


def test_load_history_digest_includes_advisory():
    """End-to-end: the digest returned by _load_history_digest should
    include the cross-loop advisory block when applicable."""
    from kairos.loop.review_loop import _load_history_digest

    class _FakePersistence:
        def __init__(self, rounds):
            self._rounds = rounds
        def load_loop_rounds(self, project_id, limit=20):
            return self._rounds

    rounds = [
        _make_round(category="correctness", score=50),
        _make_round(category="correctness", score=51),
        _make_round(category="correctness", score=50),
    ]
    digest = _load_history_digest(_FakePersistence(rounds), "p1")
    assert "Previous loop history" in digest
    assert "CROSS-LOOP ADVISORY" in digest
    assert "correctness" in digest


def test_load_history_digest_empty_persistence():
    from kairos.loop.review_loop import _load_history_digest
    assert _load_history_digest(None, "p1") == ""

    class _EmptyPersistence:
        def load_loop_rounds(self, project_id, limit=20):
            return []
    assert _load_history_digest(_EmptyPersistence(), "p1") == ""
# ---------------------------------------------------------------- loop health + auto-route

def test_loop_health_score_healthy():
    from kairos.loop.review_loop import _loop_health_score
    class S:
        score_window = [60, 70, 80]
        infra_failure_streak = 0
        no_progress_count = 0
    assert _loop_health_score(S()) >= 90


def test_loop_health_score_stagnating():
    from kairos.loop.review_loop import _loop_health_score
    class S:
        score_window = [50, 50, 50]
        infra_failure_streak = 2
        no_progress_count = 0
    h = _loop_health_score(S())
    assert 60 <= h <= 90


def test_loop_health_score_critical():
    from kairos.loop.review_loop import _loop_health_score
    class S:
        score_window = [80, 60, 40]
        infra_failure_streak = 5
        no_progress_count = 5
    assert _loop_health_score(S()) <= 20


def test_loop_health_score_empty_window():
    """Loop that has not scored yet should still report healthy (100)."""
    from kairos.loop.review_loop import _loop_health_score
    class S:
        score_window = []
        infra_failure_streak = 0
        no_progress_count = 0
    assert _loop_health_score(S()) == 100


def test_auto_route_security_keywords():
    """Requirements mentioning auth/login/oauth should auto-enable
    the security reviewer."""
    from kairos.core.orchestrator import _auto_route_specialists
    assert "security_reviewer" in _auto_route_specialists(
        "Build a login page with OAuth and JWT"
    )
    assert "security_reviewer" in _auto_route_specialists(
        "implement user login and permissions"
    )


def test_auto_route_perf_keywords():
    from kairos.core.orchestrator import _auto_route_specialists
    assert "perf_reviewer" in _auto_route_specialists(
        "Optimize API latency, add cache layer"
    )


def test_auto_route_design_and_test():
    from kairos.core.orchestrator import _auto_route_specialists
    assert "design_reviewer" in _auto_route_specialists(
        "Make the UI look nice with custom CSS animations"
    )
    assert "test_reviewer" in _auto_route_specialists(
        "Add pytest unit tests with high coverage"
    )


def test_auto_route_no_match():
    """Requirements that match no specialist keyword should return empty."""
    from kairos.core.orchestrator import _auto_route_specialists
    assert _auto_route_specialists("") == []
    assert _auto_route_specialists("fix typo in readme") == []


def test_auto_route_multi_match():
    """Requirements matching multiple specialists should return all of them
    so the user gets full coverage of relevant concerns."""
    from kairos.core.orchestrator import _auto_route_specialists
    result = _auto_route_specialists(
        "Build an authenticated API with optimized performance, "
        "a nice UI, and full test coverage"
    )
    assert "security_reviewer" in result
    assert "perf_reviewer" in result
    assert "design_reviewer" in result
    assert "test_reviewer" in result


# ---------------------------------------------------------------- self-debug + revert_file

def test_build_next_prompt_self_debug_block_when_precheck_fixable():
    """When precheck found known fixes, the next Coder prompt must include
    a SELF-DEBUG block that takes priority over Reviewer issues."""
    import asyncio
    from kairos.core.message_bus import MessageBus
    from kairos.loop import review_loop as rl

    class StubProject:
        def __init__(self):
            self.id = "p1"
            self.requirements = "fix login"

    class StubAgent:
        async def run(self, task, plan_mode=False):
            return ""

    session = rl.LoopSession(
        project=StubProject(),
        message_bus=MessageBus(),
        coder=StubAgent(),
        reviewer=StubAgent(),
        persistence=None,
    )
    session.original_requirement = "fix login"
    review = {
        "approve": False, "score": 30, "summary": "still bad",
        "issues": [], "_precheck_hint": "[PRECHECK FAILURES] ModuleNotFound",
        "_precheck_fixable": [
            {"kind": "missing_module", "match": "No module named 'pandas'",
             "fix_instruction": "pip install pandas"},
        ],
    }
    prompt = rl._build_next_prompt(session, review)
    assert "SELF-DEBUG MODE" in prompt
    assert "pip install pandas" in prompt
    assert prompt.index("SELF-DEBUG MODE") < prompt.index("PRECHECK FAILURES")


def test_revert_file_via_git(tmp_path: Path):
    """revert_file restores a single file's content; other files untouched."""
    from kairos.tools.checkpoint import (
        checkpoint_round, ensure_repo, revert_file,
    )
    ensure_repo(tmp_path)
    (tmp_path / "a.py").write_text("version1")
    (tmp_path / "b.py").write_text("untouched")
    sha1 = checkpoint_round(tmp_path, 1, 80, "v1", True)
    (tmp_path / "a.py").write_text("version2")
    sha2 = checkpoint_round(tmp_path, 2, 80, "v2", True)

    ok, err = revert_file(tmp_path, sha1, "a.py")
    assert ok, err
    assert (tmp_path / "a.py").read_text() == "version1"
    # b.py unchanged
    assert (tmp_path / "b.py").read_text() == "untouched"


def test_orchestrator_review_focus_not_specialists():
    """Orchestrator must no longer build multiple specialist Reviewer
    agents; it should expose a single Reviewer with a review_focus list."""
    from kairos.core.orchestrator import Orchestrator, _load_loop_config, _auto_route_specialists

    focus = _auto_route_specialists("please add login authentication with OAuth and JWT")
    assert "security" in focus
    assert "performance" not in focus

    # A synthetic loop_config without specialists still produces a focus list
    cfg = _load_loop_config()
    assert isinstance(cfg.get("review_focus"), list)