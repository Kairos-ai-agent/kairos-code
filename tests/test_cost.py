"""Tests for the kairos.cost module."""
from __future__ import annotations

import time

import pytest

from kairos.cost import (
    CostTracker,
    DEFAULT_PRICING,
    ProjectCostSummary,
    UsageRecord,
    _normalize_model,
    estimate_cost,
    get_tracker,
    reset_tracker,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_tracker()
    yield
    reset_tracker()


# ---------------------------------------------------------------------------
# Model name normalization
# ---------------------------------------------------------------------------


def test_normalize_strips_provider_prefix():
    assert _normalize_model("openai/gpt-4o") == "gpt-4o"
    assert _normalize_model("anthropic/claude-3-5-sonnet") == "claude-3-5-sonnet"


def test_normalize_strips_date_suffix():
    assert _normalize_model("gpt-4o-2024-08-06") == "gpt-4o"
    assert _normalize_model("claude-3-5-sonnet-20241022") == "claude-3-5-sonnet"


def test_normalize_handles_case():
    assert _normalize_model("GPT-4o") == "gpt-4o"


def test_normalize_empty():
    assert _normalize_model("") == ""


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


def test_estimate_gpt4o():
    # gpt-4o: $2.50 in / $10.00 out per 1M tokens
    # 1M input + 1M output = 2.50 + 10.00 = 12.50
    cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
    assert abs(cost - 12.50) < 1e-6


def test_estimate_claude_sonnet():
    cost = estimate_cost("claude-3-5-sonnet", 500_000, 200_000)
    # 0.5M * 3.00 = 1.50; 0.2M * 15.00 = 3.00; total 4.50
    assert abs(cost - 4.50) < 1e-6


def test_estimate_unknown_model_is_zero():
    assert estimate_cost("no-such-model-xyz", 1000, 1000) == 0.0


def test_estimate_ollama_is_free():
    assert estimate_cost("ollama", 10_000_000, 10_000_000) == 0.0


def test_estimate_with_provider_prefix():
    # openai/gpt-4o-2024-08-06 normalizes to gpt-4o
    cost = estimate_cost("openai/gpt-4o-2024-08-06", 1_000_000, 1_000_000)
    assert abs(cost - 12.50) < 1e-6


def test_estimate_negative_tokens_clamped_to_zero():
    assert estimate_cost("gpt-4o", -100, 100) == 0.0
    assert estimate_cost("gpt-4o", 100, -100) == 0.0


def test_custom_pricing_overrides_default():
    custom = {"foo": {"input": 1.0, "output": 2.0}}
    cost = estimate_cost("foo", 1_000_000, 1_000_000, pricing=custom)
    assert cost == 3.0


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


def test_record_returns_usage_record():
    t = CostTracker()
    rec = t.record(
        project_id="p1", agent="coder", role="code",
        model="gpt-4o", prompt_tokens=100, completion_tokens=50,
    )
    assert isinstance(rec, UsageRecord)
    assert rec.project_id == "p1"
    assert rec.cost_usd > 0


def test_record_collects_in_memory():
    t = CostTracker()
    t.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=100, completion_tokens=50)
    t.record(project_id="p1", agent="reviewer", role="review", model="gpt-4o",
             prompt_tokens=200, completion_tokens=100)
    t.record(project_id="p2", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=300, completion_tokens=150)
    assert len(t.all_records()) == 3


def test_by_project_aggregates():
    t = CostTracker()
    t.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=100, completion_tokens=50)
    t.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=200, completion_tokens=100)
    t.record(project_id="p2", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=50, completion_tokens=25)
    s = t.by_project()
    assert set(s.keys()) == {"p1", "p2"}
    assert s["p1"].prompt_tokens == 300
    assert s["p1"].completion_tokens == 150
    assert s["p1"].calls == 2
    assert s["p2"].calls == 1


def test_by_project_by_agent_breakdown():
    t = CostTracker()
    t.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=100, completion_tokens=50)
    t.record(project_id="p1", agent="reviewer", role="review", model="gpt-4o",
             prompt_tokens=200, completion_tokens=100)
    t.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=50, completion_tokens=25)
    s = t.by_project()["p1"]
    assert s.by_agent == {"coder": 2, "reviewer": 1}


def test_by_project_by_model_breakdown():
    t = CostTracker()
    t.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=100, completion_tokens=50)
    t.record(project_id="p1", agent="reviewer", role="review", model="claude-3-5-sonnet",
             prompt_tokens=200, completion_tokens=100)
    s = t.by_project()["p1"]
    assert s.by_model == {"gpt-4o": 1, "claude-3-5-sonnet": 1}


def test_summary_global():
    t = CostTracker()
    t.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=100, completion_tokens=50)
    t.record(project_id="p2", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=200, completion_tokens=100)
    s = t.summary()
    assert s["calls"] == 2
    assert s["prompt_tokens"] == 300
    assert s["completion_tokens"] == 150
    assert s["total_tokens"] == 450


def test_clear_resets_tracker():
    t = CostTracker()
    t.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=100, completion_tokens=50)
    assert len(t.all_records()) == 1
    t.clear()
    assert len(t.all_records()) == 0


def test_set_pricing_overrides_default():
    t = CostTracker()
    t.set_pricing({"foo": {"input": 1.0, "output": 2.0}})
    rec = t.record(project_id="p1", agent="coder", role="code", model="foo",
                   prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert abs(rec.cost_usd - 3.0) < 1e-6


def test_get_pricing_returns_copy():
    t = CostTracker()
    p = t.get_pricing()
    p["bogus"] = {"input": 0, "output": 0}
    # mutating the returned dict must not affect the tracker's state
    assert "bogus" not in t.get_pricing()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


def test_get_tracker_returns_singleton():
    a = get_tracker()
    b = get_tracker()
    assert a is b


def test_reset_tracker_replaces_singleton():
    a = get_tracker()
    a.record(project_id="p1", agent="coder", role="code", model="gpt-4o",
             prompt_tokens=100, completion_tokens=50)
    reset_tracker()
    b = get_tracker()
    assert a is not b
    assert len(b.all_records()) == 0


# ---------------------------------------------------------------------------
# ProjectCostSummary
# ---------------------------------------------------------------------------


def test_project_summary_to_dict_shape():
    s = ProjectCostSummary(project_id="p1")
    s.prompt_tokens = 100
    s.completion_tokens = 50
    s.cost_usd = 0.001
    s.calls = 1
    s.by_agent = {"coder": 1}
    d = s.to_dict()
    assert d["project_id"] == "p1"
    assert d["total_tokens"] == 150
    assert d["cost_usd"] == 0.001
    assert d["by_agent"] == {"coder": 1}


# ---------------------------------------------------------------------------
# Concurrent safety
# ---------------------------------------------------------------------------


def test_tracker_thread_safe():
    import threading
    t = CostTracker()

    def worker():
        for _ in range(50):
            t.record(project_id="p", agent="coder", role="code", model="gpt-4o",
                     prompt_tokens=10, completion_tokens=5)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    # 8 * 50 = 400 records
    assert len(t.all_records()) == 400
    s = t.by_project()["p"]
    assert s.calls == 400
    assert s.prompt_tokens == 4000
    assert s.completion_tokens == 2000
