"""Tests for the two commands that make Kairos dispatchable.

``kairos worker`` is identity (one repo, one session, never a new one per
task); ``kairos accept`` is the part that has to swallow a task document of
any shape. Both are exercised through ``main()`` the way a caller would drive
them, because the exit code is the contract.

No model is used anywhere here: ``--no-model`` is the path a caller with no
key gets, and it must still produce a usable record.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairos.cli import (
    EXIT_BAD_INPUT,
    EXIT_NEEDS_CONFIRMATION,
    EXIT_OK,
    main,
)


def _doc(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# kairos worker — identity
# ---------------------------------------------------------------------------


def test_attach_binds_a_repo_once(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()

    assert main(["worker", "attach", "--repo", str(repo)]) == EXIT_OK
    first = capsys.readouterr().out
    assert "project id" in first
    assert (repo / ".kairos" / "worker.json").exists()

    assert main(["worker", "attach", "--repo", str(repo)]) == EXIT_OK
    second = capsys.readouterr().out

    project_ids = {
        json.loads((repo / ".kairos" / "worker.json").read_text(
            encoding="utf-8"))["project_id"]
    }
    assert len(project_ids) == 1
    assert "dispatches : 0" in first and "dispatches : 0" in second


def test_attach_json_is_machine_readable(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()

    assert main(["worker", "attach", "--repo", str(repo), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_id"].startswith("w")
    assert payload["repo"].endswith("proj")


def test_status_reports_unbound_then_bound(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()

    assert main(["worker", "status", "--repo", str(repo)]) == EXIT_OK
    assert "no worker bound" in capsys.readouterr().out

    main(["worker", "attach", "--repo", str(repo)])
    capsys.readouterr()

    assert main(["worker", "status", "--repo", str(repo), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["bound"] is True
    assert payload["project_id"]


def test_forget_detaches_and_is_idempotent(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    main(["worker", "attach", "--repo", str(repo)])
    capsys.readouterr()

    assert main(["worker", "forget", "--repo", str(repo)]) == EXIT_OK
    assert "detached" in capsys.readouterr().out
    assert main(["worker", "forget", "--repo", str(repo)]) == EXIT_OK
    assert "no worker was bound" in capsys.readouterr().out


def test_attach_refuses_a_directory_that_does_not_exist(tmp_path, capsys):
    missing = tmp_path / "nope"
    assert main(["worker", "attach", "--repo", str(missing)]) == EXIT_BAD_INPUT
    assert "no such directory" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# kairos accept — any document, no format required
# ---------------------------------------------------------------------------


def test_accept_reads_prose_and_records_what_it_understood(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    doc = _doc(repo, "T001-auth.md",
               "把登录接口的错误信息改成中文，跑 pytest tests/test_auth.py -q 验证。\n")

    code = main(["accept", str(doc), "--repo", str(repo), "--no-model"])
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "我这样理解的" in out
    assert "T001" in out

    outbox = repo / ".kairos" / "outbox"
    assert (outbox / "T001.understanding.md").exists()
    record = json.loads((outbox / "T001.intake.json").read_text(encoding="utf-8"))
    assert record["task_id"] == "T001"
    assert record["units"]
    assert record["extracted_by"] == "heuristic"


def test_accept_handles_a_document_with_no_structure_at_all(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    doc = _doc(repo, "notes.md", "make the export stop mangling utf-8\n")

    assert main(["accept", str(doc), "--repo", str(repo),
                 "--no-model"]) == EXIT_OK
    records = list((repo / ".kairos" / "outbox").glob("*.intake.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["units"]


def test_accept_asks_before_doing_something_dangerous(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    doc = _doc(repo, "T009-danger.md",
               "清空 orders 表并重写 .env 里的凭据。\n")

    code = main(["accept", str(doc), "--repo", str(repo), "--no-model"])
    out = capsys.readouterr().out

    assert code == EXIT_NEEDS_CONFIRMATION
    assert "需要你确认" in out
    questions = repo / ".kairos" / "outbox" / "T009.questions.md"
    assert questions.exists()
    assert "不需要任何格式" in questions.read_text(encoding="utf-8")


def test_accept_reports_an_empty_document_as_bad_input(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    doc = _doc(repo, "empty.md", "   \n\n")

    assert main(["accept", str(doc), "--repo", str(repo),
                 "--no-model"]) == EXIT_BAD_INPUT
    assert "空" in capsys.readouterr().out


def test_accept_refuses_a_missing_file(tmp_path, capsys):
    assert main(["accept", str(tmp_path / "nope.md"),
                 "--no-model"]) == EXIT_BAD_INPUT
    assert "no such task document" in capsys.readouterr().err


def test_accept_json_mode_emits_the_record(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    doc = _doc(repo, "T003-x.md", "## 改配置\n把超时从 30s 调到 60s\n")

    assert main(["accept", str(doc), "--repo", str(repo), "--no-model",
                 "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == "T003"
    assert payload["schema_version"] == 1
    assert payload["units"][0]["title"].startswith("改配置")


def test_accept_writes_nothing_outside_the_repo(tmp_path, capsys):
    """Our record is bookkeeping, not a deliverable: it stays under .kairos/,
    which the project's own .gitignore keeps out of the diff."""
    repo = tmp_path / "proj"
    repo.mkdir()
    doc = _doc(repo, "T004-y.md", "调整日志级别\n")

    main(["accept", str(doc), "--repo", str(repo), "--no-model"])
    capsys.readouterr()

    on_disk = {p.name for p in repo.iterdir()}
    assert on_disk == {"T004-y.md", ".kairos"}


def test_parser_lists_the_new_subcommands():
    from kairos.cli import build_parser

    parser = build_parser()
    choices = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            choices.update(action.choices)
    assert {"worker", "accept"} <= choices


@pytest.mark.parametrize("argv", [
    ["worker", "--help"],
    ["accept", "--help"],
])
def test_help_works_without_a_repo(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 0
