"""Tests for the orchestrator's manual skills reload."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_reload_skills_returns_zero_for_unknown_project():
    from kairos.core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch._projects = {}  # bypass __init__ for the unit test
    result = orch.reload_skills("missing-id")
    assert result == {"count": 0, "names": [], "error": "project_not_found"}


def test_reload_skills_handles_project_without_work_dir():
    from kairos.core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    project = type("P", (), {"id": "p1"})()
    orch._projects = {"p1": project}
    result = orch.reload_skills("p1")
    assert result["count"] == 0
    assert result["names"] == []
    assert "error" in result


def test_reload_skills_discovers_md_files(tmp_path: Path):
    """A real .kairos/skills/ tree on disk is found by reload_skills."""
    from kairos.core.orchestrator import Orchestrator
    skills_dir = tmp_path / ".kairos" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "debug.md").write_text(
        "---\nname: debug\npriority: 0.9\n---\n\nUse pdb.", encoding="utf-8"
    )
    (skills_dir / "lint.md").write_text(
        "---\nname: lint\npriority: 0.5\n---\n\nRun ruff.", encoding="utf-8"
    )

    orch = Orchestrator.__new__(Orchestrator)
    project = type("P", (), {"id": "p1", "work_dir": str(tmp_path)})()
    orch._projects = {"p1": project}
    result = orch.reload_skills("p1")
    assert result["count"] == 2
    assert set(result["names"]) == {"debug", "lint"}


def test_reload_skills_handles_missing_skills_dir(tmp_path: Path):
    """Work dir exists but has no .kairos/skills/ → count=0, no error."""
    from kairos.core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    project = type("P", (), {"id": "p1", "work_dir": str(tmp_path)})()
    orch._projects = {"p1": project}
    result = orch.reload_skills("p1")
    assert result["count"] == 0
    assert result["names"] == []
