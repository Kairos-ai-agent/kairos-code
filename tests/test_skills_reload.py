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
        "---" + chr(10) + "name: debug" + chr(10) + "priority: 0.9" + chr(10) + "---" + chr(10) + chr(10) + "Use pdb.",
        encoding="utf-8",
    )
    (skills_dir / "lint.md").write_text(
        "---" + chr(10) + "name: lint" + chr(10) + "priority: 0.5" + chr(10) + "---" + chr(10) + chr(10) + "Run ruff.",
        encoding="utf-8",
    )

    orch = Orchestrator.__new__(Orchestrator)
    project = type("P", (), {"id": "p1", "work_dir": str(tmp_path)})()
    orch._projects = {"p1": project}
    # skip_bundled=True to scope the test to project-local skills only
    result = orch.reload_skills("p1", skip_bundled=True)
    assert result["count"] == 2
    assert set(result["names"]) == {"debug", "lint"}


def test_reload_skills_handles_missing_skills_dir(tmp_path: Path):
    """Work dir exists but no .kairos/skills/ -> count=0, no error."""
    from kairos.core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    project = type("P", (), {"id": "p1", "work_dir": str(tmp_path)})()
    orch._projects = {"p1": project}
    # skip_bundled=True because the test isn't about the bundled skills
    result = orch.reload_skills("p1", skip_bundled=True)
    assert result["count"] == 0
    assert result["names"] == []

# ---------------------------------------------------------------------------
# Bundled skills (round 9) — superpowers drop-in via kairos/skills/
# ---------------------------------------------------------------------------


def test_bundled_skills_discover_default_loader():
    """The default SkillsLoader picks up kairos/skills/superpowers__*.md
    even when neither ~/.kairos/skills nor a project_dir is set.
    Confirms the bundled-scope is on by default."""
    from kairos.skills import SkillsLoader
    loader = SkillsLoader()
    skills = loader.discover()
    names = {s.name for s in skills}
    expected = {
        "brainstorming", "systematic-debugging",
        "test-driven-development", "verification-before-completion",
        "writing-plans",
    }
    missing = expected - names
    assert not missing, f"missing bundled skills: {missing}"


def test_bundled_skills_have_priority_above_user_default():
    """Bundled discipline skills should outrank user ad-hoc skills (0.5)."""
    from kairos.skills import SkillsLoader
    loader = SkillsLoader()
    for s in loader.discover():
        if s.name in {"test-driven-development", "systematic-debugging"}:
            assert s.priority >= 0.7, (
                f"{s.name} priority {s.priority} should be >= 0.7 "
                f"to outrank user ad-hoc skills at 0.5"
            )


def test_bundled_skill_tdd_matches_implementing_feature():
    """test-driven-development has a `when.keyword` block that should
    match the contextual `implementing ... feature ... code` strings
    that show up in real LLM prompts. We use a higher max_active to
    see the full candidate set — the default top-3 may cut TDD if
    several other "implementing"-keyword skills also match."""
    from kairos.skills import SkillsLoader
    loader = SkillsLoader(max_active=20)
    matched = loader.match({
        "task_title": "Write a test for the new feature first",
        "description": "Implementing bugfix with TDD approach",
        "tools": ["file_write"],
    })
    names = {s.name for s in matched}
    assert "test-driven-development" in names, (
        f"TDD skill should match a 'writing test for feature / TDD' prompt, got {names}"
    )


def test_project_dir_overrides_bundled_skill(tmp_path):
    """A project-scope .kairos/skills/ file with the same name wins
    over the bundled one — the later scope overwrites the dict."""
    from kairos.skills import SkillsLoader
    project = tmp_path / "proj"
    proj_skills = project / ".kairos" / "skills"
    proj_skills.mkdir(parents=True)
    (proj_skills / "test-driven-development.md").write_text(
        "---\n"
        "name: test-driven-development\n"
        "description: Project override — TDD is forbidden here\n"
        "priority: 0.0\n"
        "when:\n"
        "  keyword: [implementing]\n"
        "---\n\n"
        "Project-local override body. We don't allow TDD.\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(project_dir=project)
    skill = next(
        s for s in loader.discover() if s.name == "test-driven-development"
    )
    assert "forbidden" in skill.description, (
        "Project-scope should override the bundled version"
    )
    assert "Project-local override" in skill.body


def test_bundled_skills_pure_dir_path(tmp_path):
    """SkillsLoader can take an explicit `bundled_dir` (used in tests
    that want to point at a different fixture)."""
    from kairos.skills import SkillsLoader
    skill = tmp_path / "my-skill.md"
    skill.write_text(
        "---\nname: foo\ndescription: x\npriority: 0.6\n---\n\nfoo body\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(
        bundled_dir=tmp_path, global_dir=tmp_path / "nope",
        project_dir=None,
    )
    names = {s.name for s in loader.discover()}
    assert "foo" in names
    assert "test-driven-development" not in names  # real bundled skipped
