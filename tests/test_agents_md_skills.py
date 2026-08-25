"""Tests for AGENTS.md + Skills (Codex Harness design patterns)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make `kairos` importable when run from tests/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.agents_md import (
    AgentsMd,
    AgentsMdLoader,
    AgentsMdSection,
    _parse_agents_md,
)
from kairos.skills import Skill, SkillsLoader, _parse_skill


# ============================================================
# agents_md
# ============================================================

def test_parse_simple_agents_md():
    text = """# Title

Some intro.

## Architecture
- Uses Flask.
- Uses SQLite.

## Don't
- No new dependencies.
"""
    md = _parse_agents_md(text, source=Path("/fake/AGENTS.md"))
    headings = [s.heading for s in md.sections]
    assert "Overview" in headings  # pre-heading content wrapped
    assert "Architecture" in headings
    assert "Don't" in headings
    arch = md.get_section("Architecture")
    assert arch is not None
    assert "Flask" in arch
    assert "SQLite" in arch


def test_parse_empty_agents_md():
    md = _parse_agents_md("", source=None)
    assert md.is_empty()
    assert md.render() == ""


def test_agents_md_loader_project_wins():
    with tempfile.TemporaryDirectory() as d:
        project_dir = Path(d)
        (project_dir / "AGENTS.md").write_text(
            "# Project\n\n## Conventions\n- Project-level rule\n",
            encoding="utf-8",
        )
        loader = AgentsMdLoader(
            project_dir=project_dir,
            global_path=Path("/nonexistent/AGENTS.md"),
        )
        md = loader.load()
        assert md.source_path == project_dir / "AGENTS.md"
        assert "Project-level rule" in md.render()


def test_agents_md_loader_falls_back_to_internal_when_no_files():
    loader = AgentsMdLoader(
        project_dir=None,
        global_path=Path("/nonexistent/AGENTS.md"),
    )
    md = loader.load()
    # Internal fallback body should be there.
    assert "Kairos Code" in md.render() or "built-in" in md.render().lower()


def test_agents_md_loader_merges_global_and_project():
    with tempfile.TemporaryDirectory() as d:
        project_dir = Path(d)
        global_path = project_dir / "global_AGENTS.md"
        project_path = project_dir / "AGENTS.md"
        global_path.write_text(
            "## Global rule\n- Global info\n",
            encoding="utf-8",
        )
        project_path.write_text(
            "## Project rule\n- Project info\n",
            encoding="utf-8",
        )
        loader = AgentsMdLoader(
            project_dir=project_dir, global_path=global_path
        )
        md = loader.load()
        text = md.render()
        assert "Global info" in text
        assert "Project info" in text
        # Project appended AFTER global
        assert text.index("Global info") < text.index("Project info")


def test_agents_md_cap_truncation():
    big = "x" * 10000
    with tempfile.TemporaryDirectory() as d:
        project_dir = Path(d)
        (project_dir / "AGENTS.md").write_text(
            f"## Big\n\n{big}\n", encoding="utf-8"
        )
        loader = AgentsMdLoader(
            project_dir=project_dir,
            global_path=Path("/nonexistent/AGENTS.md"),
            file_cap_bytes=200,
        )
        md = loader.load()
        text = md.render(cap_bytes=400)
        assert "truncated" in text
        assert len(text) < 600


def test_merge_into_system_prompt_appends():
    loader = AgentsMdLoader(
        project_dir=None, global_path=Path("/nonexistent/AGENTS.md")
    )
    base = "You are a Coder."
    out = loader.merge_into_system_prompt(base)
    assert out.startswith("You are a Coder.")
    assert "AGENTS.md" in out


# ============================================================
# skills
# ============================================================

def test_skill_matches_no_when_always_true():
    s = Skill(name="always", body="body")
    assert s.matches({}) is True
    assert s.matches({"any": "context"}) is True


def test_skill_keyword_match():
    s = Skill(name="react", when={"keyword": "react"}, body="")
    assert s.matches({"description": "Build a React component"}) is True
    assert s.matches({"description": "Build a Vue component"}) is False


def test_skill_tools_match():
    s = Skill(name="write-files", when={"tools": ["file_write"]}, body="")
    assert s.matches({"tools": ["file_write", "file_read"]}) is True
    assert s.matches({"tools": ["terminal"]}) is False


def test_skill_globs_match():
    s = Skill(name="tsx", when={"globs": ["*.tsx", "*.jsx"]}, body="")
    assert s.matches({"filename": "App.tsx"}) is True
    assert s.matches({"filename": "App.py"}) is False


def test_skill_match_combines_with_and_semantics():
    """When has multiple keys, all must match."""
    s = Skill(
        name="react-ui",
        when={"keyword": "react", "globs": ["*.tsx"]},
        body="",
    )
    assert s.matches({"description": "react app", "filename": "App.tsx"})
    assert not s.matches({"description": "vue app", "filename": "App.tsx"})
    assert not s.matches({"description": "react app", "filename": "App.py"})


def test_parse_skill_with_frontmatter(tmp_path):
    p = tmp_path / "react.md"
    p.write_text(
        """---
name: react-hooks
description: React 18 hooks
when:
  keyword: react
  globs:
    - "*.tsx"
priority: 0.7
---

# React Hooks

Don't do X.
""",
        encoding="utf-8",
    )
    s = _parse_skill(p)
    assert s is not None
    assert s.name == "react-hooks"
    assert s.description == "React 18 hooks"
    assert s.priority == 0.7
    assert s.when["keyword"] == "react"
    assert "Don't do X" in s.body


def test_parse_skill_no_frontmatter_returns_none(tmp_path):
    p = tmp_path / "nope.md"
    p.write_text("# Just a doc\n\nNo frontmatter here.\n")
    assert _parse_skill(p) is None


def test_skills_loader_project_wins_on_name(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".kairos").mkdir()
    skills_dir = proj / ".kairos" / "skills"
    skills_dir.mkdir()
    g_dir = tmp_path / "global"
    g_dir.mkdir()

    # Same skill name in both, different bodies.
    (g_dir / "foo.md").write_text(
        """---
name: foo
description: global version
---
global body
""",
        encoding="utf-8",
    )
    (skills_dir / "foo.md").write_text(
        """---
name: foo
description: project version
---
project body
""",
        encoding="utf-8",
    )
    loader = SkillsLoader(project_dir=proj, global_dir=g_dir)
    skills = loader.discover()
    assert len(skills) == 1
    assert skills[0].description == "project version"
    assert "project body" in skills[0].body


def test_skills_loader_match_returns_top_n_by_priority(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    proj_skills = proj / ".kairos" / "skills"
    proj_skills.mkdir(parents=True)
    for i, p in enumerate([0.9, 0.1, 0.5, 0.7]):
        (proj_skills / f"skill{i}.md").write_text(
            f"""---
name: skill{i}
when:
  keyword: hello
priority: {p}
---
body {i}
""",
            encoding="utf-8",
        )
    loader = SkillsLoader(project_dir=proj, max_active=2)
    matched = loader.match({"description": "say hello world"})
    assert len(matched) == 2
    priorities = [s.priority for s in matched]
    assert priorities == sorted(priorities, reverse=True)
    assert priorities[0] == 0.9


def test_skills_loader_render(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    skills_dir = proj / ".kairos" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "demo.md").write_text(
        """---
name: demo
description: demo skill
---
this is the body
""",
        encoding="utf-8",
    )
    loader = SkillsLoader(project_dir=proj)
    out = loader.render(loader.match({}))
    assert "# Active Skills" in out
    assert "## demo" in out
    assert "demo skill" in out
    assert "this is the body" in out


def test_skills_loader_for_context_integration(tmp_path):
    """The one-shot for_context() returns a renderable string."""
    proj = tmp_path / "proj"
    proj.mkdir()
    skills_dir = proj / ".kairos" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "x.md").write_text(
        """---
name: x
when:
  keyword: foo
---
bar
""",
        encoding="utf-8",
    )
    loader = SkillsLoader(project_dir=proj)
    out = loader.for_context({"description": "needs foo"})
    assert "bar" in out
    # No match → empty string
    assert loader.for_context({"description": "nothing matches"}) == ""
