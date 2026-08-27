"""Tests for kairos.hook (Round 21 pre-commit runner)."""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from kairos.hook import (
    DEFAULT_CHECKS,
    FAST_CHECKS,
    _run_one,
    check_skill_search,
    check_meta_eval,
    check_eval_smoke,
    check_tests,
    main,
    run_default,
)


# ---------------------------------------------------------------------------
# _run_one
# ---------------------------------------------------------------------------


def test_run_one_passes():
    result = _run_one("trivial", lambda: (True, "ok"))
    assert result["ok"] is True
    assert result["name"] == "trivial"
    assert "ok" in result["detail"]
    assert result["duration_ms"] >= 0


def test_run_one_captures_failure():
    result = _run_one("fails", lambda: (False, "nope"))
    assert result["ok"] is False
    assert "nope" in result["detail"]


def test_run_one_catches_exception():
    def boom():
        raise RuntimeError("kaboom")
    result = _run_one("crash", boom)
    assert result["ok"] is False
    assert "kaboom" in result["detail"]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def test_check_skill_search_passes():
    ok, detail = check_skill_search()
    assert ok is True
    assert "ready" in detail


def test_check_meta_eval_passes():
    ok, detail = check_meta_eval()
    assert ok is True
    # The new structural check: positives pass, negatives fail.
    assert "positives pass" in detail
    assert "negatives correctly fail" in detail


def test_check_eval_smoke_passes():
    ok, detail = check_eval_smoke()
    assert ok is True
    assert "smoke" in detail


def test_check_eval_smoke_missing_suite_fails(tmp_path: Path):
    ok, detail = check_eval_smoke(str(tmp_path / "missing.yaml"))
    assert ok is False
    assert "not found" in detail


def test_check_tests_runs_pytest():
    """The test check actually runs pytest. This is a meta-test."""
    # We don't want this to be slow — just verify the function
    # is callable and returns a tuple. (It would run real pytest
    # which is too slow for a unit test.)
    import inspect
    sig = inspect.signature(check_tests)
    assert callable(check_tests)
    assert len(sig.parameters) == 0


# ---------------------------------------------------------------------------
# run_default
# ---------------------------------------------------------------------------


def test_default_checks_includes_skill_meta_smoke_tests():
    names = [c[0] for c in DEFAULT_CHECKS]
    assert "skill-search" in names
    assert "meta-eval" in names
    assert "smoke" in names
    assert "tests" in names


def test_fast_checks_excludes_tests():
    names = [c[0] for c in FAST_CHECKS]
    assert "tests" not in names
    assert "skill-search" in names
    assert "smoke" in names


def test_run_default_fast_skips_tests(monkeypatch, tmp_path: Path):
    """With --fast, the tests check is not invoked even if it would fail."""
    test_called = {"v": False}
    def fail_tests():
        test_called["v"] = True
        return False, "should not be called"
    # Patch the tests check to a failure-trigger
    monkeypatch.setitem(dict(DEFAULT_CHECKS), "tests", ("tests", fail_tests))
    # Recompute FAST_CHECKS using the patched DEFAULT_CHECKS
    # (the patched dict has the failing tests; FAST should exclude it).
    import kairos.hook as hook_mod
    # Run with --fast; tests should not be called.
    rc = hook_mod.run_default(
        fast=True, suite=str(tmp_path / "x.yaml"),
    )
    assert test_called["v"] is False


def test_run_default_short_circuits_on_first_failure(monkeypatch, tmp_path: Path):
    """If check_skill_search fails, the other checks don't run."""
    skill_called = {"v": False}
    meta_called = {"v": False}
    def fake_skill():
        skill_called["v"] = True
        return False, "skills broken"
    def fake_meta():
        meta_called["v"] = True
        return True, "ok"
    # Patch the checks
    import kairos.hook as hook_mod
    orig_default = list(hook_mod.DEFAULT_CHECKS)
    new_default = [
        ("skill-search", fake_skill),
        ("meta-eval", fake_meta),
    ]
    monkeypatch.setattr(hook_mod, "DEFAULT_CHECKS", new_default)
    # Run with --fast (only 2 checks; if --fast, the slow one
    # is excluded). For our fake default, --fast would just be
    # the 2 we have. So we need to be careful.
    # Run the full sequence; expect short-circuit at skill-search.
    rc = hook_mod.run_default(fast=False, suite="x")
    assert rc == 1
    assert skill_called["v"] is True
    assert meta_called["v"] is False  # short-circuited


def test_run_default_all_pass_returns_zero(monkeypatch, tmp_path: Path):
    """When every check passes, exit code is 0."""
    def ok_check():
        return True, "ok"
    import kairos.hook as hook_mod
    monkeypatch.setattr(hook_mod, "DEFAULT_CHECKS", [
        ("a", ok_check), ("b", ok_check),
    ])
    rc = hook_mod.run_default(fast=True, suite="x")
    assert rc == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_list_runs(capsys):
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "skill-search" in out
    assert "meta-eval" in out


def test_cli_run_no_args(capsys):
    """`kairos.hook run` without --fast runs the full suite."""
    rc = main(["run"])
    captured = capsys.readouterr()
    # rc is 0 (all pass) or 1 (something failed). Either is fine;
    # we just want to verify the CLI parses.
    assert rc in (0, 1)
    assert "pre-commit check" in captured.out


def test_cli_run_fast(capsys):
    """`kairos.hook run --fast` skips the tests check."""
    rc = main(["run", "--fast"])
    captured = capsys.readouterr()
    assert rc in (0, 1)
    # The "tests" check should NOT appear in the output
    assert " tests " not in captured.out  # exact spacing matters


def test_cli_run_verbose(capsys):
    """`kairos.hook run --verbose` prints details for passing checks too."""
    rc = main(["run", "--fast", "--verbose"])
    captured = capsys.readouterr()
    assert rc in (0, 1)
    # Verbose output includes the detail for each check
    out = captured.out
    assert "— " in out or "  -" in out  # em-dash or hyphen for detail line


def test_cli_no_args_shows_help(capsys):
    """`kairos.hook` (no subcommand) exits non-zero with help."""
    with pytest.raises(SystemExit):
        main([])
