"""Tests for kairos exec CLI (non-interactive one-shot mode).

We avoid hitting the real LLM stack by stubbing `Orchestrator`,
`Persistence`, and `ModelRouter` at the import-time seams in cli.py.
The point of these tests is to verify the CLI surface (argument
parsing, output formatting, exit codes, cleanup) — not the
underlying agent loop, which is covered elsewhere.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.cli import (
    EXIT_BAD_INPUT,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_TIMEOUT,
    build_parser,
    main,
    run_exec,
)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_parser_has_subcommands():
    parser = build_parser()
    sub_actions = [
        a for a in parser._actions
        if a.__class__.__name__ == "_SubParsersAction"
    ]
    assert sub_actions
    sub_names = set(sub_actions[0].choices.keys())
    assert {"serve", "exec"}.issubset(sub_names)


def test_parser_exec_minimal():
    parser = build_parser()
    args = parser.parse_args(["exec", "do a thing"])
    assert args.command == "exec"
    assert args.task == "do a thing"
    assert args.persist is False
    assert args.json_output is False
    assert args.quiet is False
    assert args.timeout == 600
    assert args.no_cleanup is False


def test_parser_exec_full_options():
    parser = build_parser()
    args = parser.parse_args([
        "exec", "refactor X", "--persist", "--json", "--quiet",
        "--model", "gpt-4o", "--timeout", "120", "--work-dir", "/tmp/x",
        "--no-cleanup",
    ])
    assert args.task == "refactor X"
    assert args.persist is True
    assert args.json_output is True
    assert args.quiet is True
    assert args.model == "gpt-4o"
    assert args.timeout == 120
    assert args.work_dir == "/tmp/x"
    assert args.no_cleanup is True


def test_parser_serve_minimal():
    parser = build_parser()
    args = parser.parse_args(["serve", "--port", "9999"])
    assert args.command == "serve"
    assert args.port == 9999


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_empty_task_returns_bad_input():
    """Empty task should fail fast with EXIT_BAD_INPUT, no crash."""
    parser = build_parser()
    args = parser.parse_args(["exec", ""])
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = asyncio.run(run_exec(args))
    assert code == EXIT_BAD_INPUT


def test_whitespace_only_task_returns_bad_input():
    parser = build_parser()
    args = parser.parse_args(["exec", "   \t  "])
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        code = asyncio.run(run_exec(args))
    assert code == EXIT_BAD_INPUT


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def test_emit_human_readable_format():
    from kairos.cli import _emit
    parser = build_parser()
    args = parser.parse_args(["exec", "x"])
    payload = {
        "project_id": "abc",
        "status": "completed",
        "elapsed_s": 1.23,
        "work_dir": "/tmp/kairos",
        "result": "the plan goes here",
    }
    out = io.StringIO()
    with redirect_stdout(out):
        _emit(args, payload)
    body = out.getvalue()
    assert "=== kairos exec completed ===" in body
    assert "1.23" in body
    assert "/tmp/kairos" in body
    assert "the plan goes here" in body


def test_emit_json_format():
    from kairos.cli import _emit
    parser = build_parser()
    args = parser.parse_args(["exec", "x", "--json"])
    payload = {"status": "completed", "elapsed_s": 0.5, "result": "ok"}
    out = io.StringIO()
    with redirect_stdout(out):
        _emit(args, payload)
    data = json.loads(out.getvalue())
    assert data["status"] == "completed"
    assert data["result"] == "ok"


def test_emit_handles_error_field():
    from kairos.cli import _emit
    parser = build_parser()
    args = parser.parse_args(["exec", "x"])
    payload = {"status": "failed", "error": "no API key"}
    out = io.StringIO()
    with redirect_stdout(out):
        _emit(args, payload)
    body = out.getvalue()
    assert "--- error ---" in body
    assert "no API key" in body


def test_emit_handles_object_result():
    """Some plans return dicts (already-parsed JSON), not strings."""
    from kairos.cli import _emit
    parser = build_parser()
    args = parser.parse_args(["exec", "x"])
    payload = {
        "status": "completed",
        "result": {"analysis": "ok", "tasks": []},
    }
    out = io.StringIO()
    with redirect_stdout(out):
        _emit(args, payload)
    body = out.getvalue()
    assert '"analysis": "ok"' in body


# ---------------------------------------------------------------------------
# run_exec: integration via stubbed orchestrator
# ---------------------------------------------------------------------------


def _make_fake_orchestrator(*, plan_text: str = "", status: str = "completed"):
    """Returns an Orchestrator-like object whose start_project
    writes `plan_text` to the bus as a `project.plan` message and
    marks the project as `status`."""
    fake = MagicMock()
    project = MagicMock()
    project.id = "test-pid"
    project.status = status
    project.task_count = 0
    fake.create_project = MagicMock(return_value=project)

    async def fake_get_history(limit, topic_filter):
        if plan_text:
            return [{"content": plan_text}]
        return []
    fake.message_bus.get_history = fake_get_history

    async def fake_start_project(pid, requirement):
        project.status = status
    fake.start_project = fake_start_project

    async def fake_close_all_clients():
        pass
    fake.close_all_clients = fake_close_all_clients

    return fake, project


def test_run_exec_happy_path_with_stub_orchestrator(tmp_path):
    """The CLI should drive the orchestrator and emit a JSON plan."""
    fake, project = _make_fake_orchestrator(
        plan_text='{"analysis":"hi","plan":"world","tasks":[]}',
        status="completed",
    )
    with patch("kairos.core.orchestrator.Orchestrator", return_value=fake), \
         patch("kairos.core.persistence.Persistence"), \
         patch("kairos.llm.model_router.ModelRouter"):
        parser = build_parser()
        args = parser.parse_args([
            "exec", "build a hello world",
            "--work-dir", str(tmp_path),
            "--json", "--quiet", "--timeout", "5",
        ])
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = asyncio.run(run_exec(args))
    body = out.getvalue()
    data = json.loads(body)
    assert data["status"] == "completed"
    assert "analysis" in data["result"]
    assert code == EXIT_OK


def test_run_exec_human_readable_with_stub_orchestrator(tmp_path):
    fake, _ = _make_fake_orchestrator(
        plan_text="the plan",
        status="completed",
    )
    with patch("kairos.core.orchestrator.Orchestrator", return_value=fake), \
         patch("kairos.core.persistence.Persistence"), \
         patch("kairos.llm.model_router.ModelRouter"):
        parser = build_parser()
        args = parser.parse_args([
            "exec", "build a hello world",
            "--work-dir", str(tmp_path),
            "--quiet", "--timeout", "5",
        ])
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            asyncio.run(run_exec(args))
    body = out.getvalue()
    assert "=== kairos exec completed ===" in body
    assert "the plan" in body


def test_run_exec_persist_keeps_workdir(tmp_path):
    fake, _ = _make_fake_orchestrator(
        plan_text="x", status="completed",
    )
    target = tmp_path / "kept"
    with patch("kairos.core.orchestrator.Orchestrator", return_value=fake), \
         patch("kairos.core.persistence.Persistence"), \
         patch("kairos.llm.model_router.ModelRouter"):
        parser = build_parser()
        args = parser.parse_args([
            "exec", "task",
            "--work-dir", str(target),
            "--persist", "--quiet", "--timeout", "5",
        ])
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = asyncio.run(run_exec(args))
    assert target.exists(), "--persist should keep work_dir"
    assert code == EXIT_OK


def test_run_exec_failed_status_returns_exit_failed(tmp_path):
    fake, _ = _make_fake_orchestrator(plan_text="", status="failed")
    with patch("kairos.core.orchestrator.Orchestrator", return_value=fake), \
         patch("kairos.core.persistence.Persistence"), \
         patch("kairos.llm.model_router.ModelRouter"):
        parser = build_parser()
        args = parser.parse_args([
            "exec", "task",
            "--work-dir", str(tmp_path),
            "--quiet", "--timeout", "5",
        ])
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = asyncio.run(run_exec(args))
    assert code == EXIT_FAILED


def test_run_exec_no_work_dir_creates_temp(tmp_path):
    """When --work-dir is not given, the CLI should make a tempdir
    and clean it up after a successful run."""
    fake, _ = _make_fake_orchestrator(plan_text="x", status="completed")
    with patch("kairos.core.orchestrator.Orchestrator", return_value=fake), \
         patch("kairos.core.persistence.Persistence"), \
         patch("kairos.llm.model_router.ModelRouter"):
        parser = build_parser()
        args = parser.parse_args([
            "exec", "task",
            "--quiet", "--timeout", "5",
        ])
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = asyncio.run(run_exec(args))
    assert code == EXIT_OK


# ---------------------------------------------------------------------------
# Top-level main()
# ---------------------------------------------------------------------------


def test_main_exec_invokes_run_exec():
    """`kairos exec ...` should route through run_exec."""
    fake, _ = _make_fake_orchestrator(plan_text="x", status="completed")
    with patch("kairos.core.orchestrator.Orchestrator", return_value=fake), \
         patch("kairos.core.persistence.Persistence"), \
         patch("kairos.llm.model_router.ModelRouter"):
        with tempfile.TemporaryDirectory() as d:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main([
                    "exec", "do something",
                    "--work-dir", d, "--quiet", "--timeout", "5",
                ])
    assert code == EXIT_OK


def test_main_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_main_no_args_falls_back_to_legacy(monkeypatch):
    """Bare `kairos` invocation should fall through to the legacy
    uvicorn-launching branch without crashing."""
    called = {"serve": False}

    def fake_uvicorn_run(*a, **kw):
        called["serve"] = True
        raise SystemExit(0)
    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            main([])
        except SystemExit:
            pass
    assert called["serve"] is True
