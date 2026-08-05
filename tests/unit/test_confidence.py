"""Tests for Reviewer confidence calibration."""
from __future__ import annotations

import json

import pytest

from kairos.core.persistence import Persistence
from kairos.loop.reviewers import _normalize_verdict
from kairos.memory.growth import (
    auto_promote_failure_to_preference,
    maybe_record_working_fix,
)


@pytest.fixture
def db(tmp_path):
    return Persistence(tmp_path / "mem.db")


# ============================================================ _normalize_verdict

def test_normalize_extracts_confidence_default_0_5():
    v = _normalize_verdict({"approve": True, "score": 80, "issues": [], "summary": "ok"})
    assert v["_confidence"] == 0.5


def test_normalize_extracts_confidence_value():
    v = _normalize_verdict({"approve": True, "score": 80, "issues": [], "summary": "ok", "_confidence": 0.9})
    assert v["_confidence"] == 0.9


def test_normalize_clamps_confidence_to_range():
    v = _normalize_verdict({"approve": True, "score": 80, "_confidence": 5.0, "issues": [], "summary": ""})
    assert v["_confidence"] == 1.0
    v = _normalize_verdict({"approve": True, "score": 80, "_confidence": -2.0, "issues": [], "summary": ""})
    assert v["_confidence"] == 0.0


def test_normalize_handles_string_confidence():
    v = _normalize_verdict({"approve": True, "score": 80, "_confidence": "0.7", "issues": [], "summary": ""})
    assert abs(v["_confidence"] - 0.7) < 1e-6


def test_normalize_handles_invalid_confidence():
    v = _normalize_verdict({"approve": True, "score": 80, "_confidence": "not a number", "issues": [], "summary": ""})
    assert v["_confidence"] == 0.5


# ============================================================ confidence gates

def test_auto_promote_skipped_when_confidence_low(db):
    issue = {"category": "correctness", "description": "missing input validation"}
    pid = auto_promote_failure_to_preference(
        db, "p1", issue, consecutive_count=3, confidence=0.3, min_confidence=0.5,
    )
    assert pid is None
    # Above threshold -> still skipped.
    pid = auto_promote_failure_to_preference(
        db, "p1", issue, consecutive_count=3, confidence=0.8,
    )
    assert pid is not None


def test_maybe_record_working_fix_skipped_on_low_confidence(db):
    prior = {"approve": False, "score": 40,
             "issues": [{"category": "correctness", "severity": "MAJOR",
                         "description": "missing null check on user input",
                         "file": "x.py", "line": 10}]}
    passing = {"approve": True, "score": 90, "issues": [], "_confidence": 0.4}
    # Low confidence -> NOT recorded.
    assert maybe_record_working_fix(db, "p1", prior, passing, "added None guard") is None
    # High confidence -> recorded.
    passing["_confidence"] = 0.9
    assert maybe_record_working_fix(db, "p1", prior, passing, "added None guard") is not None
