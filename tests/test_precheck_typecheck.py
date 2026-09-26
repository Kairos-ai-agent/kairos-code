"""The precheck's type check: config-driven, and never a network call.

Codex-style evidence means types, not just lint and tests. The two rules that
matter are that an unconfigured project gets no type check at all (an
unconfigured mypy reports hundreds of pre-existing complaints the Coder cannot
distinguish from its own), and that nothing here can reach the network -- `npx
tsc` on a project without node_modules downloads the compiler, which turns a
static check into a hang behind a firewall.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kairos.loop import precheck

MYPY = "C:/tools/mypy.exe"
NODE = "C:/node/node.exe"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n",
                                             encoding="utf-8")
    (tmp_path / "m.py").write_text("x: int = 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    """Record every command the precheck would run, and fake the result."""
    seen: list = []

    async def fake_run(cmd, cwd, timeout=60):
        seen.append((list(cmd), timeout))
        return {"ok": True, "code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(precheck, "_run", fake_run)
    return seen


def _which(mapping):
    def fake(name):
        return mapping.get(name)
    return fake


def run(ws, files=("m.py",)):
    return asyncio.run(precheck.pre_check_workspace(
        ws, list(files), test_command=[]))


# ---------------------------------------------------------------------------
# python: mypy, only where it is configured
# ---------------------------------------------------------------------------

def test_mypy_runs_on_the_changed_files(ws, calls, monkeypatch):
    monkeypatch.setattr(precheck.shutil, "which", _which({"mypy": MYPY}))
    res = run(ws)
    assert res["types"] is not None and res["types"]["ok"] is True
    cmd, timeout = calls[0]
    assert cmd[0] == "mypy"
    assert "m.py" in cmd
    assert "--follow-imports=silent" in cmd
    assert timeout == 90
    assert "types" in res["summary"]
    assert res["has_failures"] is False


def test_a_project_without_a_mypy_config_gets_no_type_check(tmp_path, calls, monkeypatch):
    """A check that cries wolf is worse than no check."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(precheck.shutil, "which", _which({"mypy": MYPY}))
    res = run(tmp_path)
    assert res["types"] is None
    assert calls == []


def test_mypy_missing_from_the_machine_is_skipped(ws, calls, monkeypatch):
    monkeypatch.setattr(precheck.shutil, "which", _which({}))
    res = run(ws)
    assert res["types"] is None
    assert calls == []


def test_failing_mypy_counts_as_a_failure_and_reaches_the_prompt(ws, monkeypatch):
    monkeypatch.setattr(precheck.shutil, "which", _which({"mypy": MYPY}))

    async def failing_run(cmd, cwd, timeout=60):
        return {"ok": False, "code": 1, "stdout": "m.py:1: error: bad type\n",
                "stderr": ""}

    monkeypatch.setattr(precheck, "_run", failing_run)
    res = run(ws)
    assert res["has_failures"] is True
    assert "fail: types" in res["summary"]
    rendered = precheck.format_precheck_for_prompt(res)
    assert "TYPES:" in rendered
    assert "bad type" in rendered


def test_mypy_and_ruff_both_run(ws, calls, monkeypatch):
    monkeypatch.setattr(precheck.shutil, "which",
                        _which({"mypy": MYPY, "ruff": "C:/tools/ruff.exe"}))
    res = run(ws)
    names = [c[0][0] for c in calls]
    assert "ruff" in names and "mypy" in names
    assert "pass: ruff" in res["summary"] and "pass: types" in res["summary"]


# ---------------------------------------------------------------------------
# typescript: the local compiler only, never npx
# ---------------------------------------------------------------------------

def _ts_project(tmp_path: Path) -> Path:
    (tmp_path / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    tsc = tmp_path / "node_modules" / "typescript" / "bin" / "tsc"
    tsc.parent.mkdir(parents=True, exist_ok=True)
    tsc.write_text("// tsc\n", encoding="utf-8")
    return tmp_path


def test_local_tsc_is_used_when_it_is_installed(tmp_path, calls, monkeypatch):
    proj = _ts_project(tmp_path)
    monkeypatch.setattr(precheck.shutil, "which", _which({"node": NODE}))
    res = run(proj, files=("app.tsx",))
    assert res["types"] is not None
    cmd, _ = calls[0]
    assert cmd == ["node", "node_modules/typescript/bin/tsc", "--noEmit"]


def test_a_project_without_typescript_installed_gets_nothing(tmp_path, calls, monkeypatch):
    """`npx tsc` would download the compiler. A static check must not do that."""
    (tmp_path / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(precheck.shutil, "which", _which({"node": NODE}))
    res = run(tmp_path, files=("app.tsx",))
    assert res["types"] is None
    assert calls == []


def test_node_missing_means_no_tsc(tmp_path, calls, monkeypatch):
    proj = _ts_project(tmp_path)
    monkeypatch.setattr(precheck.shutil, "which", _which({}))
    res = run(proj, files=("app.tsx",))
    assert res["types"] is None
    assert calls == []


def test_the_precheck_never_invokes_npx(tmp_path, calls, monkeypatch):
    proj = _ts_project(tmp_path)
    monkeypatch.setattr(precheck.shutil, "which",
                        _which({"node": NODE, "mypy": MYPY, "ruff": "ruff"}))
    run(proj, files=("m.py", "app.tsx"))
    for cmd, _ in calls:
        assert "npx" not in cmd, cmd
