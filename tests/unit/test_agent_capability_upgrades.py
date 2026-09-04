"""Tests for the agent-capability upgrade set:

- kairos.memory.semantic.rank_by_similarity (TF-IDF cosine re-ranker)
- MemoryKB.recall semantic fallback
- kairos.loop.gates.dynamic_caps (adaptive loop ceilings)
- kairos.loop.loop_runner._calibrate_review (test-evidence calibration)
- kairos.loop.loop_runner._objective_signal (execution evidence)
- kairos.loop.plan.plan_deviation_report + loop-runner deviation write
- kairos.llm.model_router.resolve_task_tier (difficulty routing)
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kairos.llm.model_router import resolve_task_tier
from kairos.loop.gates import (
    COST_TOKEN_CAP,
    LOOP_SAFETY_CAP,
    dynamic_caps,
)
from kairos.loop.loop_runner import (
    _calibrate_review,
    _objective_signal,
    _record_plan_deviation,
)
from kairos.loop.plan import Plan, TodoItem, plan_deviation_report
from kairos.memory.semantic import rank_by_similarity

# ---------------------------------------------------------------------------
# semantic.rank_by_similarity
# ---------------------------------------------------------------------------


class TestRankBySimilarity:
    def test_related_doc_outranks_unrelated(self):
        docs = [
            "fix the database connection timeout",
            "update the readme title",
        ]
        ranked = rank_by_similarity("database timeout error", docs)
        assert ranked, "expected at least one doc above min_score"
        assert ranked[0][0] == 0

    def test_cjk_bigram_matching(self):
        docs = ["数据库连接超时需要修复", "更新项目说明文档"]
        ranked = rank_by_similarity("数据库超时问题", docs)
        assert ranked and ranked[0][0] == 0

    def test_min_score_filters_noise(self):
        ranked = rank_by_similarity("quantum flux capacitor", ["the sky is blue"])
        assert ranked == []

    def test_top_k_limits_results(self):
        docs = ["alpha beta", "alpha gamma", "delta epsilon"]
        ranked = rank_by_similarity("alpha", docs, top_k=2)
        assert len(ranked) <= 2

    def test_empty_inputs(self):
        assert rank_by_similarity("x", []) == []
        assert rank_by_similarity("", ["doc"]) == []

    def test_indices_preserve_original_order_for_ties(self):
        docs = ["same text", "same text"]
        ranked = rank_by_similarity("same", docs)
        assert [i for i, _ in ranked] == [0, 1]


# ---------------------------------------------------------------------------
# MemoryKB.recall semantic fallback
# ---------------------------------------------------------------------------


class TestMemoryKBSemanticRecall:
    @pytest.fixture
    def kb(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
        from kairos.memory_kb import MemoryKB

        return MemoryKB(storage_path=tmp_path / "memory" / "kb.json")

    def test_exact_recall_unchanged(self, kb):
        kb.remember("db-timeout", "database connection timeout fix")
        hits = kb.recall("timeout", scope="project", limit=5)
        assert any(e.key == "db-timeout" for e in hits)

    def test_semantic_fallback_finds_rephrased_match(self, kb):
        kb.remember("db-note", "the database layer keeps dropping sockets")
        kb.remember("style-note", "prefer black formatting for all files")
        # No shared token with "database dropping sockets" phrasing in
        # the query below beyond scattered words; semantic phase should
        # rank db-note above style-note.
        hits = kb.recall("storage layer losing connections", limit=2)
        assert hits, "semantic fallback should return something"
        assert hits[0].key == "db-note"

    def test_no_query_returns_nothing(self, kb):
        kb.remember("a", "1")
        kb.remember("b", "2")
        # Pre-existing behavior: an empty query matches nothing.
        assert kb.recall("", limit=10) == []


# ---------------------------------------------------------------------------
# gates.dynamic_caps
# ---------------------------------------------------------------------------


class TestDynamicCaps:
    def test_small_requirement_keeps_defaults(self):
        caps = dynamic_caps("fix typo in readme")
        assert caps["safety_cap"] == LOOP_SAFETY_CAP
        assert caps["token_cap"] == COST_TOKEN_CAP

    def test_heavy_keyword_doubles_caps(self):
        caps = dynamic_caps("please refactor the whole auth module")
        assert caps["safety_cap"] == LOOP_SAFETY_CAP * 2
        assert caps["token_cap"] == COST_TOKEN_CAP * 2

    def test_long_requirement_doubles_caps(self):
        caps = dynamic_caps("x" * 2500)
        assert caps["safety_cap"] > LOOP_SAFETY_CAP

    def test_big_plan_doubles_caps(self):
        caps = dynamic_caps("small task", plan_items=10)
        assert caps["safety_cap"] == LOOP_SAFETY_CAP * 2

    def test_caps_are_clamped(self):
        caps = dynamic_caps("重构 架构 迁移 重写 " * 500)
        assert caps["safety_cap"] <= 200
        assert caps["token_cap"] <= 2_000_000

    def test_empty_requirement_is_default(self):
        caps = dynamic_caps("")
        assert caps["safety_cap"] == LOOP_SAFETY_CAP


# ---------------------------------------------------------------------------
# loop_runner._calibrate_review
# ---------------------------------------------------------------------------


class TestCalibrateReview:
    def test_parse_verdict_preserves_tests_evidence(self):
        from kairos.loop.reviewers import parse_review_verdict

        raw = json.dumps({
            "approve": True,
            "score": 91,
            "tests_evidence": {"ran": True, "command": "pytest -q", "passed": True},
            "issues": [],
            "summary": "clean",
        })
        verdict = parse_review_verdict(raw)
        assert verdict["tests_evidence"]["ran"] is True

    def test_surviving_evidence_allows_approval(self):
        # The full pipeline: evidence survives normalization and the
        # calibration step does NOT downgrade a verified verdict.
        review = {
            "approve": True,
            "score": 91,
            "tests_evidence": {"ran": True, "command": "pytest", "passed": True},
            "issues": [],
        }
        out = _calibrate_review(review, require_evidence=True)
        assert out["approve"] is True
        assert out["score"] == 91

    def test_parallel_merge_skips_calibration(self):
        review = {
            "approve": True,
            "score": 90,
            "_per_reviewer": {"reviewer": {"approve": True, "score": 90}},
        }
        out = _calibrate_review(review, require_evidence=True)
        assert out["approve"] is True
        assert out["score"] == 90

    def test_noop_when_disabled(self):
        review = {"approve": True, "score": 95, "issues": []}
        out = _calibrate_review(review, require_evidence=False)
        assert out["approve"] is True
        assert out["score"] == 95

    def test_noop_when_evidence_present(self):
        review = {
            "approve": True,
            "score": 90,
            "tests_evidence": {"ran": True, "command": "pytest", "passed": True},
            "issues": [],
        }
        out = _calibrate_review(review, require_evidence=True)
        assert out["approve"] is True
        assert out["score"] == 90

    def test_missing_evidence_blocks_approval(self):
        review = {"approve": True, "score": 92, "issues": []}
        out = _calibrate_review(review, require_evidence=True)
        assert out["approve"] is False
        assert out["score"] <= 84
        assert out["_calibration"]["missing_test_evidence"] is True
        severities = [i["severity"] for i in out["issues"]]
        assert "MAJOR" in severities

    def test_falsy_evidence_blocks_approval(self):
        review = {
            "approve": False,
            "score": 88,
            "tests_evidence": {"ran": False},
            "issues": [],
        }
        out = _calibrate_review(review, require_evidence=True)
        assert out["score"] <= 84
        assert any("test suite" in i["fix_instruction"] for i in out["issues"])

    def test_non_dict_passthrough(self):
        assert _calibrate_review("not a dict", require_evidence=True) == "not a dict"


# ---------------------------------------------------------------------------
# loop_runner._objective_signal
# ---------------------------------------------------------------------------


class TestObjectiveSignal:
    def test_passing_tests_score_positive(self):
        assert _objective_signal("pytest -q\n12 passed in 1.2s") > 0

    def test_failing_tests_score_negative(self):
        assert _objective_signal("pytest -q\n2 failed, 10 passed") < 0

    def test_all_green_phrase(self):
        assert _objective_signal("all tests passed, exit code 0") > 0

    def test_zero_failed_is_not_a_failure(self):
        # "0 failed" / "0 errors" must not be mistaken for a failure.
        assert _objective_signal("Tests: 10 passed, 0 failed, 0 skipped") > 0
        assert _objective_signal("go test ./... -> 0 errors") == 0

    def test_no_signal_is_zero(self):
        assert _objective_signal("I refactored the module and everything looks good") == 0
        assert _objective_signal("") == 0


# ---------------------------------------------------------------------------
# plan deviation
# ---------------------------------------------------------------------------


class TestPlanDeviation:
    def test_report_shape(self):
        plan = Plan(todos=[
            TodoItem(status="completed", content="A"),
            TodoItem(status="pending", content="B"),
        ])
        rep = plan_deviation_report(plan)
        assert rep["total"] == 2
        assert rep["completed"] == ["A"]
        assert rep["pending"] == ["B"]
        assert rep["completion"] == pytest.approx(0.5)

    def test_fully_completed_reported_as_clean(self):
        plan = Plan(todos=[TodoItem(status="completed", content="A")])
        rep = plan_deviation_report(plan)
        assert rep["completion"] == 1.0
        assert rep["pending"] == []

    def test_record_plan_deviation_writes_memory_kb(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
        project = SimpleNamespace(id="proj-1")
        session = SimpleNamespace(
            project=project,
            session_id="session-abcdef-1234",
            round=4,
            plan_todos=Plan(todos=[
                TodoItem(status="completed", content="done thing"),
                TodoItem(status="pending", content="never finished"),
            ]),
        )
        _record_plan_deviation(session)
        from kairos.memory_kb import MemoryKB

        kb = MemoryKB(storage_path=tmp_path / "memory" / "kb.json")
        key = "plan-deviation:proj-1:session-"  # sid[:8]
        entry = kb.get(key, scope="project")
        assert entry is not None
        assert entry.value["pending"] == ["never finished"]
        assert entry.value["rounds"] == 4

    def test_record_skips_when_fully_complete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
        project = SimpleNamespace(id="proj-2")
        session = SimpleNamespace(
            project=project,
            session_id="sid",
            round=2,
            plan_todos=Plan(todos=[TodoItem(status="completed", content="A")]),
        )
        _record_plan_deviation(session)
        from kairos.memory_kb import MemoryKB

        kb = MemoryKB(storage_path=tmp_path / "memory" / "kb.json")
        assert kb.get("plan-deviation:proj-2:sid", scope="project") is None

    def test_record_skips_empty_plan(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KAIROS_DATA_DIR", str(tmp_path))
        session = SimpleNamespace(
            project=SimpleNamespace(id="p"),
            session_id="s",
            round=1,
            plan_todos=Plan(),
        )
        _record_plan_deviation(session)  # must not raise


# ---------------------------------------------------------------------------
# model_router.resolve_task_tier
# ---------------------------------------------------------------------------


class TestResolveTaskTier:
    def test_short_simple_is_fast(self):
        assert resolve_task_tier("fix the typo in README") == "fast"

    def test_empty_is_fast(self):
        assert resolve_task_tier("") == "fast"

    def test_heavy_keyword_is_strong(self):
        assert resolve_task_tier("migrate the storage layer to postgres") == "strong"

    def test_long_brief_is_strong(self):
        assert resolve_task_tier("x" * 2500) == "strong"

    def test_medium_is_default(self):
        text = "Add a CSV export endpoint. " * 10
        assert resolve_task_tier(text) == "default"

    def test_cjk_heavy_keyword(self):
        assert resolve_task_tier("请重构整个认证模块") == "strong"
