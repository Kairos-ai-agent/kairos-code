"""Tests for the community skills adapter (R34).

Covers:
  - extract_keywords (same logic as R31/R33, regression-tested)
  - adapt_skill (priority 0.6, when: block, provenance footer)
  - main() end-to-end against a temp directory
  - PREFIX = "community__" so the names don't collide with anthropic/superpowers
  - SkillsLoader picks up community__*.md files
  - FTS5 search finds the community skills
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from scripts.adapt_community_skills import (
    DEFAULT_PRIORITY,
    PREFIX,
    adapt_skill,
    extract_keywords,
    main,
)


# ---------------------------------------------------------------------------
# extract_keywords
# ---------------------------------------------------------------------------


def test_extract_keywords_dedups():
    desc = "test test test word word word other other"
    kws = extract_keywords(desc, max_kw=10)
    assert len(kws) == len(set(kws))


def test_extract_keywords_respects_max_kw():
    desc = "alpha beta gamma delta epsilon zeta"
    kws = extract_keywords(desc, max_kw=3)
    assert len(kws) == 3


def test_extract_keywords_strips_stop_words():
    desc = "this is a test for the user"
    kws = extract_keywords(desc, max_kw=10)
    for s in ("the", "a", "for", "is", "this"):
        assert s not in kws


# ---------------------------------------------------------------------------
# adapt_skill
# ---------------------------------------------------------------------------


def _make_skill(tmp_path: Path, name: str, body: str) -> Path:
    skill_dir = tmp_path / "vendor" / "_oss" / "claude-skills" / "engineering-team" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = f'---\nname: "{name}"\ndescription: A test skill for {name}.\n---\n\n{body}'
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir / "SKILL.md"


def test_adapt_skill_preserves_body(tmp_path: Path):
    src = _make_skill(tmp_path, "demo", "# Body\n\nOriginal content here.\n")
    dst = tmp_path / "out" / f"{PREFIX}demo.md"
    name, kws = adapt_skill(src, dst)
    assert name == "demo"
    out = dst.read_text(encoding="utf-8")
    assert "# Body" in out
    assert "Original content here." in out


def test_adapt_skill_adds_priority_0_6(tmp_path: Path):
    """R34 priority is 0.6 (lower than superpowers 0.8 + anthropic 0.7)."""
    src = _make_skill(tmp_path, "demo",
                      "A test skill for building web applications.")
    dst = tmp_path / "out" / f"{PREFIX}demo.md"
    adapt_skill(src, dst)
    out = dst.read_text(encoding="utf-8")
    assert f"priority: {DEFAULT_PRIORITY}" in out
    assert "priority: 0.6" in out  # R34-specific value


def test_adapt_skill_adds_when_block(tmp_path: Path):
    src = _make_skill(tmp_path, "demo",
                      "A test skill for building web applications.")
    dst = tmp_path / "out" / f"{PREFIX}demo.md"
    adapt_skill(src, dst)
    out = dst.read_text(encoding="utf-8")
    assert "when:" in out
    assert "keyword:" in out


def test_adapt_skill_provenance_cites_upstream(tmp_path: Path):
    src = _make_skill(tmp_path, "demo", "body")
    dst = tmp_path / "out" / f"{PREFIX}demo.md"
    adapt_skill(src, dst)
    out = dst.read_text(encoding="utf-8")
    assert "Adapted from alirezarezvani/claude-skills" in out
    assert "MIT" in out
    assert "https://github.com/alirezarezvani/claude-skills" in out
    assert "scripts/adapt_community_skills.py" in out


def test_adapt_skill_handles_quoted_name(tmp_path: Path):
    """alirezarezvani skills often quote the name field: name: \"foo\".
    Strip the quotes when writing to the Kairos frontmatter."""
    src = _make_skill(tmp_path, "demo", "body")
    dst = tmp_path / "out" / f"{PREFIX}demo.md"
    adapt_skill(src, dst)
    out = dst.read_text(encoding="utf-8")
    # No leftover quotes
    assert 'name: "demo"' not in out
    assert "name: demo" in out


def test_adapt_skill_handles_quoted_description(tmp_path: Path):
    skill_dir = tmp_path / "vendor" / "_oss" / "claude-skills" / "engineering-team" / "skills" / "x"
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = '---\nname: x\ndescription: "A skill with: colons in it"\n---\n\nbody'
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    src = skill_dir / "SKILL.md"
    dst = tmp_path / "out" / f"{PREFIX}x.md"
    adapt_skill(src, dst)
    out = dst.read_text(encoding="utf-8")
    # Quotes are stripped
    assert 'description: "A skill' not in out
    assert "description: A skill with: colons in it" in out


def test_adapt_skill_is_idempotent(tmp_path: Path):
    src = _make_skill(tmp_path, "demo", "body")
    dst = tmp_path / "out" / f"{PREFIX}demo.md"
    adapt_skill(src, dst)
    first = dst.read_text(encoding="utf-8")
    adapt_skill(src, dst)
    second = dst.read_text(encoding="utf-8")
    assert first == second


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_writes_to_dst(tmp_path: Path, monkeypatch):
    from scripts import adapt_community_skills as acs
    monkeypatch.setattr(acs, "SRC", tmp_path / "vendor" / "_oss" / "claude-skills" / "engineering-team" / "skills")
    monkeypatch.setattr(acs, "DST", tmp_path / "out")
    _make_skill(tmp_path, "alpha", "alpha body")
    _make_skill(tmp_path, "beta", "beta body")
    rc = acs.main()
    assert rc == 0
    out = tmp_path / "out"
    assert (out / f"{PREFIX}alpha.md").exists()
    assert (out / f"{PREFIX}beta.md").exists()


def test_main_missing_src_errors(tmp_path: Path, monkeypatch):
    from scripts import adapt_community_skills as acs
    monkeypatch.setattr(acs, "SRC", tmp_path / "does-not-exist")
    monkeypatch.setattr(acs, "DST", tmp_path / "out")
    rc = acs.main()
    assert rc == 1


def test_main_skips_skills_without_skill_md(tmp_path: Path, monkeypatch):
    """A directory with no SKILL.md is silently skipped."""
    from scripts import adapt_community_skills as acs
    src = tmp_path / "vendor" / "_oss" / "claude-skills" / "engineering-team" / "skills"
    src.mkdir(parents=True)
    (src / "broken").mkdir()
    # No SKILL.md in broken/
    (src / "good").mkdir()
    (src / "good" / "SKILL.md").write_text(
        "---\nname: good\ndescription: x\n---\n\nbody",
        encoding="utf-8",
    )
    monkeypatch.setattr(acs, "SRC", src)
    monkeypatch.setattr(acs, "DST", tmp_path / "out")
    rc = acs.main()
    assert rc == 0
    assert (tmp_path / "out" / f"{PREFIX}good.md").exists()
    assert not (tmp_path / "out" / f"{PREFIX}broken.md").exists()


# ---------------------------------------------------------------------------
# PREFIX guard
# ---------------------------------------------------------------------------


def test_prefix_is_community():
    """PREFIX must be 'community__' so names don't collide with anthropic/superpowers."""
    assert PREFIX == "community__"


# ---------------------------------------------------------------------------
# Production: real adapted files exist and load
# ---------------------------------------------------------------------------


def test_r34_skills_in_production():
    """The 3 R34 community skills must be loaded, wherever they live.

    R38.12: skills shipped twice — a flat ``community__<name>.md`` carrying the
    trigger metadata and sometimes a ``<name>/SKILL.md`` carrying the body — and
    the loader keys by name, so one silently replaced the other. The merge
    removed the duplicates, which left these three in two different layouts, so
    ask the loader where each one is instead of assuming a path.
    """
    from kairos.skills import SkillsLoader
    from pathlib import Path  # local: this module only imports it elsewhere
    by_name = {s.name: s for s in SkillsLoader().discover()}
    for name in ("senior-architect", "tdd-guide", "code-reviewer"):
        assert name in by_name, f"{name} should be loaded"
        text = Path(str(by_name[name].source_path)).read_text(encoding="utf-8")
        assert "priority: 0.6" in text
        assert "Adapted from alirezarezvani/claude-skills" in text
        # The trigger is what makes the skill fire; a merge must not lose it.
        assert "when:" in text, f"{name} lost its trigger in the merge"


def test_r34_skills_discoverable_by_loader():
    """SkillsLoader.discover() must return the 3 R34 skills."""
    from kairos.skills import SkillsLoader
    loader = SkillsLoader()
    names = {s.name for s in loader.discover()}
    for n in ("senior-architect", "tdd-guide", "code-reviewer"):
        assert n in names, f"{n} should be discoverable"


def test_r34_skills_priority_is_0_6():
    """Community skills must have priority 0.6 (between user ad-hoc 0.5 and anthropic 0.7)."""
    from kairos.skills import SkillsLoader
    loader = SkillsLoader()
    by_name = {s.name: s for s in loader.discover()}
    for n in ("senior-architect", "tdd-guide", "code-reviewer"):
        assert by_name[n].priority == 0.6


# ---------------------------------------------------------------------------
# Doctor / R32 integration
# ---------------------------------------------------------------------------


def test_r34_skills_counted_by_doctor():
    """The R32 doctor check_skills_loadable must see the 3 R34 skills."""
    from kairos.doctor import check_skills_loadable
    r = check_skills_loadable()
    assert r.status == "ok"
    n = int(r.message.split()[0])
    # 14 superpowers + 9 anthropic (R33) + 3 community (R34) = 26
    assert n >= 26, f"expected >= 26 skills, got {n}"


# ---------------------------------------------------------------------------
# FTS5 search end-to-end
# ---------------------------------------------------------------------------


def test_fts5_finds_community_skills(tmp_path: Path, monkeypatch):
    """FTS5 search must return community__* results."""
    from kairos.skill_search import fts5_available, build_index_from_loader, search
    from kairos.skills import SkillsLoader
    if not fts5_available():
        pytest.skip("FTS5 not available")
    # Plant a community file in a temp bundled dir
    (tmp_path / "community__test-skill.md").write_text(
        "---\nname: test-skill\ndescription: a search test\npriority: 0.6\n---\n\n"
        "playwright test framework",
        encoding="utf-8",
    )
    loader = SkillsLoader(bundled_dir=tmp_path)
    db = tmp_path / "idx.db"
    build_index_from_loader(loader, db)
    results = search("playwright", loader=loader, db_path=db, limit=5)
    assert any("test-skill" in r.get("name", "") for r in results)
