"""Tests for session compaction."""
from __future__ import annotations

import pytest

from kairos.compaction import (
    CompactedDigest,
    DEFAULT_KEEP_RECENT,
    DEFAULT_THRESHOLD_ROUNDS,
    build_digest,
    compaction_stats,
    maybe_compact,
)


def _round(round_no: int, score: float, approve: bool = False,
           issues=None) -> dict:
    return {
        "round": round_no,
        "review": {
            "score": score,
            "approve": approve,
            "issues": issues or [],
            "summary": f"round {round_no} summary",
        },
        "coder_summary": f"coder output for round {round_no}",
    }


# ---------------------------------------------------------------------------
# maybe_compact
# ---------------------------------------------------------------------------


def test_maybe_compact_below_threshold_is_noop():
    history = [_round(i, 50.0) for i in range(1, 6)]  # 5 rounds
    out = maybe_compact(history)
    assert out == history
    assert len(out) == 5


def test_maybe_compact_above_threshold_folds_oldest():
    history = [_round(i, 50.0 + i) for i in range(1, 16)]  # 15 rounds
    out = maybe_compact(history, threshold=12, keep_recent=5)
    # 1 compaction record + 5 recent = 6 entries
    assert len(out) == 6
    # First entry is the compaction digest
    assert out[0].get("type") == "compaction"
    # Recent 5 are unchanged
    assert out[1:] == history[-5:]


def test_maybe_compact_digest_includes_score_stats():
    history = [_round(i, 50.0 + i * 5) for i in range(1, 14)]
    out = maybe_compact(history, threshold=12, keep_recent=4)
    d = CompactedDigest.from_dict(out[0])
    # 9 rounds folded (13 - 4 = 9)
    assert d.rounds == 9
    # score_min = 55, score_max = 95
    assert d.score_min == 55.0
    assert d.score_max == 95.0
    # average matches
    assert 70.0 <= d.score_avg <= 75.0
    assert d.score_trend == [s for s in (50.0 + i * 5 for i in range(1, 10))]


def test_maybe_compact_digest_collects_top_issues():
    history = [
        _round(1, 50, issues=[
            {"severity": "high", "category": "security"},
            {"severity": "low", "category": "style"},
        ]),
        _round(2, 60, issues=[
            {"severity": "high", "category": "security"},
            {"severity": "med", "category": "perf"},
        ]),
        _round(3, 70, issues=[
            {"severity": "high", "category": "security"},
        ]),
    ]
    out = maybe_compact(history, threshold=3, keep_recent=0)
    d = CompactedDigest.from_dict(out[0])
    # "high:security" appears 3x — must be the top signature
    assert any("high:security(3x)" in s for s in d.issues_signature)
    # last_approve = False (no round was approved)
    assert d.last_approve is False


def test_maybe_compact_summary_string_is_informative():
    history = [_round(i, 70, approve=(i > 5)) for i in range(1, 8)]
    out = maybe_compact(history, threshold=5, keep_recent=2)
    summary = out[0]["summary"]
    assert "Compacted 5 round" in summary
    assert "scores" in summary
    assert "approved" in summary


def test_maybe_compact_does_not_mutate_input():
    history = [_round(i, 60.0) for i in range(1, 16)]
    before = list(history)
    maybe_compact(history, threshold=10, keep_recent=4)
    assert history == before


def test_maybe_compact_empty_history():
    assert maybe_compact([]) == []


def test_maybe_compact_handles_already_compacted():
    """If the first entry is already a compaction digest, the
    compaction pipeline still works (skips the digest, folds the
    rest)."""
    history = [
        _round(1, 50, approve=True),
        _round(2, 60, approve=True),
        _round(3, 70, approve=True),
        _round(4, 80, approve=True),
    ]
    # First compact: folds all 4 into 1
    out1 = maybe_compact(history, threshold=4, keep_recent=1)
    assert len(out1) == 2
    # Second compact: doesn't re-fold the digest
    history2 = out1 + [_round(5, 90, approve=True)]
    out2 = maybe_compact(history2, threshold=4, keep_recent=2)
    # The new run sees 3 entries; threshold 4 → no compaction
    assert len(out2) == 3


# ---------------------------------------------------------------------------
# build_digest
# ---------------------------------------------------------------------------


def test_build_digest_empty():
    d = build_digest([])
    assert d.rounds == 0
    assert d.summary == "(empty)"


def test_build_digest_score_aggregation():
    rounds = [_round(i, float(i * 10)) for i in range(1, 5)]  # 10, 20, 30, 40
    d = build_digest(rounds)
    assert d.score_min == 10.0
    assert d.score_max == 40.0
    assert d.score_avg == 25.0


def test_build_digest_handles_malformed_review():
    """Rounds with non-dict or missing review shouldn't crash."""
    rounds = [
        _round(1, 50),
        {"round": 2, "review": "not a dict"},  # malformed
        _round(3, 70),
    ]
    d = build_digest(rounds)
    assert d.rounds == 3
    # score defaults to 0 for the malformed one
    assert 0.0 in d.score_trend


def test_build_digest_no_issues():
    rounds = [_round(1, 50, issues=None), _round(2, 60, issues=[])]
    d = build_digest(rounds)
    assert d.issues_signature == []


# ---------------------------------------------------------------------------
# compaction_stats
# ---------------------------------------------------------------------------


def test_compaction_stats_under_threshold():
    history = [_round(i, 50) for i in range(1, 6)]
    s = compaction_stats(history)
    assert s["history_rounds"] == 5
    assert s["would_compact"] is False
    assert s["rounds_folded"] == 0
    assert s["rounds_kept"] == 5


def test_compaction_stats_over_threshold():
    history = [_round(i, 50) for i in range(1, 16)]
    s = compaction_stats(history)
    assert s["history_rounds"] == 15
    assert s["would_compact"] is True
    assert s["rounds_folded"] == 15 - DEFAULT_KEEP_RECENT
    assert s["rounds_kept"] == DEFAULT_KEEP_RECENT


# ---------------------------------------------------------------------------
# Round-trip: digest -> dict -> from_dict
# ---------------------------------------------------------------------------


def test_digest_roundtrip():
    d = CompactedDigest(
        rounds=5, rounds_covered=[1, 2, 3, 4, 5],
        score_min=10.0, score_max=90.0, score_avg=50.0,
        score_trend=[10, 30, 50, 70, 90],
        issues_signature=["high:sec(2x)"],
        last_approve=True, summary="ok",
    )
    d2 = CompactedDigest.from_dict(d.to_dict())
    assert d2.rounds == d.rounds
    assert d2.rounds_covered == d.rounds_covered
    assert d2.score_avg == d.score_avg
    assert d2.score_trend == d.score_trend
    assert d2.last_approve == d.last_approve
    assert d2.summary == d.summary
    assert d2.issues_signature == d.issues_signature


# ---------------------------------------------------------------------------
# Integration: the compaction function is pure (no LLM call)
# ---------------------------------------------------------------------------


def test_compaction_is_pure_no_llm_call():
    """Compaction should NOT call the LLM. We verify this by
    not providing an LLM at all."""
    history = [_round(i, 50) for i in range(1, 20)]
    out = maybe_compact(history)
    # If it tried to call an LLM it would have raised by now.
    assert isinstance(out, list)
    assert out[0]["type"] == "compaction"


def test_compaction_default_thresholds_match():
    """The default keep_recent must be < threshold so a
    threshold-sized history actually triggers compaction."""
    assert DEFAULT_KEEP_RECENT < DEFAULT_THRESHOLD_ROUNDS
