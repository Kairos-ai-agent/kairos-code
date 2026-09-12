"""Tests for `kairos demo` — the zero-key walkthrough of the review gate.

The demo is a product promise ("see the gate in a minute, no API key"), so the
assertions here are about the promise being literally true:

  * it completes **offline** (every socket connect is made to raise);
  * it produces a real gate outcome (3 rounds, 2 rejections, final approval);
  * the final state is verified by running the repo's own tests;
  * the run leaves nothing behind except the Gate Report;
  * no environment variable or cost-ledger path is left mutated.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from kairos import cost as cost_mod
from kairos import demo as demo_mod


@pytest.fixture
def offline(monkeypatch):
    """Make any *outbound* connection raise — the demo must not need one.

    We deliberately do not patch ``socket.socket.connect``: asyncio's proactor
    event loop creates its self-pipe with ``socketpair()``, which connects
    internally, so blocking it breaks the loop before the demo even starts.
    DNS resolution + ``create_connection`` to a non-local host covers every real
    network call.
    """
    local = {"127.0.0.1", "localhost", "::1", "", None}
    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host in local:
            return real_getaddrinfo(host, *args, **kwargs)
        raise AssertionError(f"the demo tried to resolve {host!r} — it must run offline")

    real_create_connection = socket.create_connection

    def guarded_create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host in local:
            return real_create_connection(address, *args, **kwargs)
        raise AssertionError(f"the demo tried to connect to {address!r}")

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / "demo"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def _run(workspace, *, keep: bool = True, **kwargs):
    """Run the demo synchronously (the module itself is asyncio-based)."""
    import asyncio

    return asyncio.run(demo_mod.run_demo(
        out_dir=workspace, keep=keep, quiet=True, progress=False, **kwargs))


def test_demo_completes_the_gate_offline(workspace, offline):
    result = _run(workspace)

    assert result.ok is True, result.detail
    assert result.rounds == 3
    assert result.rejected_rounds == 2
    assert result.first_pass is False
    assert result.final_score == 100
    assert result.tests_ok is True, result.tests_summary
    assert "passed" in result.tests_summary or "PASS" in result.tests_summary
    assert result.coder_calls > 0 and result.reviewer_calls > 0
    assert "3 round(s)" in result.badge
    assert Path(result.report_path).exists()


def test_demo_fixes_the_code_it_was_given(workspace, offline):
    result = _run(workspace)
    relay = (Path(result.workspace) / "relay.py").read_text(encoding="utf-8")
    # all three planted bugs are gone
    assert "2 ** -attempt" not in relay
    assert "max_retries + 1" not in relay
    assert "delays[-1] = max(" in relay


def test_demo_is_fast(workspace, offline):
    result = _run(workspace)
    assert result.seconds < 60, f"the demo promised ~a minute, took {result.seconds:.1f}s"


def test_demo_report_is_a_self_contained_bilingual_artifact(workspace, offline):
    result = _run(workspace)
    html = Path(result.report_path).read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "http://" not in html and "https://" not in html
    assert 'data-en="Rounds"' in html and 'data-zh="轮次"' in html
    assert "R1" in html and "R3" in html


def test_demo_report_matches_the_loop_events(workspace, offline):
    """The receipt must agree with what the gate actually decided."""
    from kairos import gate_report

    result = _run(workspace)
    report = gate_report.collect(result.project_id,
                                db_path=Path(result.workspace).parent / "kairos.db")
    assert report.rounds_total == result.rounds == 3
    assert report.rejected_rounds == 2
    assert report.score_curve == [40, 80, 100]
    assert report.state() == "passed"
    assert report.issues_total == 4          # 3 bugs in round 1 + 1 in round 2
    assert report.learned.get("fixes", 0) >= 1  # the fail→pass transition was learned


def test_demo_ledger_records_the_calls_at_zero_cost(workspace, offline):
    result = _run(workspace)
    assert result.cost_usd == 0.0            # a scripted model bills nothing
    from kairos import gate_report
    report = gate_report.collect(result.project_id,
                                db_path=Path(result.workspace).parent / "kairos.db")
    assert report.cost_calls >= 3
    assert set(report.cost_by_model) == {"scripted-coder", "scripted-reviewer"}
    assert all(slot["cost_usd"] == 0.0 for slot in report.cost_by_model.values())


def test_demo_preflight_uses_the_doctor_checks(workspace, offline):
    result = _run(workspace)
    names = {check["name"] for check in result.preflight}
    assert {"Python version", "Data dir", "Workspace dir", "Git"} <= names
    assert all(check["status"] != "fail" for check in result.preflight)


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
def test_demo_restores_env_and_cost_log(workspace, offline):
    before_skip = os.environ.get("KAIROS_SKIP_WORKTREES")
    before_log = cost_mod._get_log_path()
    _run(workspace)
    assert os.environ.get("KAIROS_SKIP_WORKTREES") == before_skip
    assert cost_mod._get_log_path() == before_log


def test_demo_keeps_the_workspace_when_asked(workspace, offline):
    result = _run(workspace)
    assert result.cleaned is False
    assert (Path(result.workspace) / "relay.py").exists()
    assert (Path(result.workspace) / "test_relay.py").exists()
    assert Path(result.report_path).parent == Path(result.workspace)


def test_demo_cleans_up_but_keeps_the_receipt(tmp_path, monkeypatch, offline):
    """Default behaviour: drop the scratch workspace, keep the Gate Report."""
    monkeypatch.chdir(tmp_path)
    result = _run(None, keep=False)
    assert result.cleaned is True
    report = Path(result.report_path)
    assert report.parent == tmp_path and report.exists()
    # nothing else was left behind in the working directory
    leftovers = [p.name for p in tmp_path.iterdir() if p != report]
    assert leftovers == [], leftovers


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_demo_cli_json_mode(tmp_path, capsys, offline):
    code = demo_mod.main(["--out", str(tmp_path / "d"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["rounds"] == 3
    assert payload["final_score"] == 100


def test_demo_cli_narration_is_bilingual(tmp_path, capsys, offline):
    demo_mod.main(["--out", str(tmp_path / "d2"), "--lang", "zh", "--quiet"])
    assert capsys.readouterr().out == ""          # --quiet stays quiet

    demo_mod.main(["--out", str(tmp_path / "d3"), "--lang", "zh"])
    out = capsys.readouterr().out
    assert "门禁" in out and "报告" in out


def test_demo_cli_reports_failure_as_exit_code_1(tmp_path, monkeypatch):
    """A broken preflight must exit non-zero instead of pretending success."""
    from kairos import doctor

    monkeypatch.setattr(doctor, "check_python_version",
                        lambda: doctor._fail("Python version", "too old"))
    code = demo_mod.main(["--out", str(tmp_path / "d4"), "--quiet"])
    assert code == 1


def test_top_level_cli_dispatches_to_demo(tmp_path, offline):
    from kairos.cli import main as cli_main

    code = cli_main(["demo", "--out", str(tmp_path / "d5"), "--json"])
    assert code == 0
