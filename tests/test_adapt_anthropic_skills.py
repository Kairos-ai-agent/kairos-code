"""Tests for the anthropic skills adapter (R31) and the SKILL.md format it produces.

Covers:
  - extract_keywords (stop words, dedup, max count)
  - adapt_skill (frontmatter injection, body preserved, provenance footer)
  - SKIP list respected
  - main() end-to-end against a temp directory
  - SkillsLoader picks up the adapted files
  - FTS5 search finds them
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.adapt_anthropic_skills import (
    DEFAULT_PRIORITY,
    adapt_skill,
    extract_keywords,
)


# ---------------------------------------------------------------------------
# extract_keywords
# ---------------------------------------------------------------------------


def test_extract_keywords_picks_salient_words():
    desc = "Guide for creating high-quality MCP servers for external APIs."
    kws = extract_keywords(desc, max_kw=6)
    # Common stop words must not appear
    for stop in ("the", "for", "and", "with"):
        assert stop not in kws
    # Salient words are present
    assert "guide" in kws
    assert "creating" in kws
    assert "servers" in kws or "apis" in kws or "external" in kws


def test_extract_keywords_dedups():
    desc = "test test test test word word word other"
    kws = extract_keywords(desc, max_kw=10)
    assert len(kws) == len(set(kws))


def test_extract_keywords_respects_max_kw():
    desc = "alpha beta gamma delta epsilon zeta eta theta"
    kws = extract_keywords(desc, max_kw=3)
    assert len(kws) == 3


def test_extract_keywords_skips_short_words():
    """Words < 3 chars should not be considered keywords."""
    desc = "I am OK with this"
    kws = extract_keywords(desc, max_kw=10)
    for k in kws:
        assert len(k) >= 3


def test_extract_keywords_handles_colons():
    """A description with colons (e.g. 'use when: ...') must not break."""
    desc = "Use this when: building MCP servers with FastAPI or Node SDK"
    kws = extract_keywords(desc, max_kw=6)
    assert "building" in kws
    assert "servers" in kws or "mcp" in kws


# ---------------------------------------------------------------------------
# adapt_skill
# ---------------------------------------------------------------------------


def _make_skill(tmp_path: Path, name: str, body: str,
                front_extra: str = "") -> Path:
    """Plant a SKILL.md in <tmp>/<name>/SKILL.md."""
    skill_dir = tmp_path / "vendor" / "_oss" / "anthropic-skills" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: A test skill for {name}.\n{front_extra}\n---\n\n{body}"
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir / "SKILL.md"


def test_adapt_skill_preserves_body(tmp_path: Path):
    src = _make_skill(tmp_path, "demo", "# Body\n\nOriginal content here.\n")
    dst = tmp_path / "out" / "anthropic__demo.md"
    name, kws = adapt_skill(src, dst)
    assert name == "demo"
    out = dst.read_text(encoding="utf-8")
    # Body is preserved verbatim
    assert "# Body" in out
    assert "Original content here." in out


def test_adapt_skill_adds_priority_and_when(tmp_path: Path):
    src = _make_skill(tmp_path, "demo",
                      "A test skill for building web applications.")
    dst = tmp_path / "out" / "anthropic__demo.md"
    name, kws = adapt_skill(src, dst)
    out = dst.read_text(encoding="utf-8")
    # New frontmatter has priority + when
    assert f"priority: {DEFAULT_PRIORITY}" in out
    assert "when:" in out
    assert "keyword:" in out


def test_adapt_skill_adds_provenance_footer(tmp_path: Path):
    src = _make_skill(tmp_path, "demo", "Body content.")
    dst = tmp_path / "out" / "anthropic__demo.md"
    adapt_skill(src, dst)
    out = dst.read_text(encoding="utf-8")
    assert "Adapted from anthropics/skills" in out
    assert "https://github.com/anthropics/skills" in out
    assert "scripts/adapt_anthropic_skills.py" in out


def test_adapt_skill_is_idempotent(tmp_path: Path):
    """Running twice produces the same file (no double provenance)."""
    src = _make_skill(tmp_path, "demo", "Body content.")
    dst = tmp_path / "out" / "anthropic__demo.md"
    adapt_skill(src, dst)
    first = dst.read_text(encoding="utf-8")
    adapt_skill(src, dst)
    second = dst.read_text(encoding="utf-8")
    # The body should be identical; the new provenance line
    # is detected and skipped the second time.
    assert first == second


def test_adapt_skill_handles_description_with_colons(tmp_path: Path):
    """A description containing colons must be read line-by-line, not
    split on the first colon (R26 lesson)."""
    skill_dir = tmp_path / "vendor" / "_oss" / "anthropic-skills" / "skills" / "x"
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        "name: x\n"
        "description: Use this when: building APIs. Or even: nested colons.\n"
        "---\n\n"
        "body"
    )
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    src = skill_dir / "SKILL.md"
    dst = tmp_path / "out" / "anthropic__x.md"
    adapt_skill(src, dst)
    out = dst.read_text(encoding="utf-8")
    assert "Use this when: building APIs. Or even: nested colons." in out


def test_adapt_skill_uses_directory_name_as_fallback(tmp_path: Path):
    """If frontmatter has no name, fall back to the directory name."""
    skill_dir = tmp_path / "vendor" / "_oss" / "anthropic-skills" / "skills" / "dirname-fallback"
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = "---\ndescription: A skill with no name in frontmatter.\n---\n\nbody"
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    src = skill_dir / "SKILL.md"
    dst = tmp_path / "out" / "anthropic__dirname-fallback.md"
    name, kws = adapt_skill(src, dst)
    assert name == "dirname-fallback"


# ---------------------------------------------------------------------------
# main() end-to-end against a temp directory
# ---------------------------------------------------------------------------


def test_main_writes_to_dst(tmp_path: Path, monkeypatch):
    """Run main() with monkeypatched SRC / DST and verify the output."""
    from scripts import adapt_anthropic_skills as aas
    monkeypatch.setattr(aas, "SRC", tmp_path / "vendor" / "_oss" / "anthropic-skills" / "skills")
    monkeypatch.setattr(aas, "DST", tmp_path / "out")

    # Plant 2 SKILL.md files
    _make_skill(tmp_path, "alpha", "Alpha body")
    _make_skill(tmp_path, "beta", "Beta body")

    rc = aas.main()
    assert rc == 0
    out = tmp_path / "out"
    assert (out / "anthropic__alpha.md").exists()
    assert (out / "anthropic__beta.md").exists()
    # Body preserved
    assert "Alpha body" in (out / "anthropic__alpha.md").read_text(encoding="utf-8")
    assert "Beta body" in (out / "anthropic__beta.md").read_text(encoding="utf-8")


def test_main_skips_listed(tmp_path: Path, monkeypatch):
    """Skills in the SKIP set are not adapted even when present in vendor."""
    from scripts import adapt_anthropic_skills as aas
    monkeypatch.setattr(aas, "SRC", tmp_path / "vendor" / "_oss" / "anthropic-skills" / "skills")
    monkeypatch.setattr(aas, "DST", tmp_path / "out")
    # internal-comms is still in SKIP as of R33
    _make_skill(tmp_path, "internal-comms", "ic body")
    _make_skill(tmp_path, "webapp-testing", "wt body")
    rc = aas.main()
    assert rc == 0
    out = tmp_path / "out"
    # internal-comms is in SKIP — must NOT be written
    assert not (out / "anthropic__internal-comms.md").exists()
    assert (out / "anthropic__webapp-testing.md").exists()


def test_main_missing_src_errors(tmp_path: Path, monkeypatch):
    from scripts import adapt_anthropic_skills as aas
    monkeypatch.setattr(aas, "SRC", tmp_path / "does-not-exist")
    monkeypatch.setattr(aas, "DST", tmp_path / "out")
    rc = aas.main()
    assert rc == 1


# ---------------------------------------------------------------------------
# SkillsLoader picks them up
# ---------------------------------------------------------------------------


def test_skills_loader_finds_adapted_anthropic_skills(tmp_path: Path, monkeypatch):
    """Plant an adapted file in the loader's bundled dir and verify
    it's discovered (R9/R22 lesson: loader honors KAIROS_DATA_DIR
    for the bundled_dir path)."""
    # Adapted file with Kairos frontmatter
    adapted = tmp_path / "anthropic__demo.md"
    adapted.write_text(
        "---\n"
        "name: demo\n"
        "description: test demo skill\n"
        f"priority: {DEFAULT_PRIORITY}\n"
        "when:\n"
        "  keyword: [test, demo]\n"
        "---\n\n"
        "Demo body content.\n"
        "<!-- Adapted from anthropics/skills (Apache-2.0) -->\n",
        encoding="utf-8",
    )
    # Use the SkillsLoader and inject the temp dir as bundled
    from kairos.skills import SkillsLoader
    loader = SkillsLoader(bundled_dir=tmp_path)
    skills = loader.discover()
    names = [s.name for s in skills]
    assert "demo" in names
    # The skill has the expected priority
    skill = next(s for s in skills if s.name == "demo")
    assert skill.priority == DEFAULT_PRIORITY


# ---------------------------------------------------------------------------
# FTS5 search finds the adapted skills
# ---------------------------------------------------------------------------


def test_fts5_search_finds_adapted_skills(tmp_path: Path, monkeypatch):
    """If FTS5 is available, a search query should hit the adapted skills."""
    from kairos.skill_search import fts5_available, build_index_from_loader, search
    from kairos.skills import SkillsLoader
    if not fts5_available():
        pytest.skip("FTS5 not available in this Python build")
    # Plant 2 adapted files
    (tmp_path / "anthropic__a.md").write_text(
        "---\nname: a\ndescription: search test skill\npriority: 0.7\n---\n\nplaywright webapp testing\n",
        encoding="utf-8",
    )
    (tmp_path / "anthropic__b.md").write_text(
        "---\nname: b\ndescription: another skill\npriority: 0.7\n---\n\nmcp server guide\n",
        encoding="utf-8",
    )
    # Build index via the loader, then search
    loader = SkillsLoader(bundled_dir=tmp_path)
    db = tmp_path / "idx.db"
    build_index_from_loader(loader, db)
    results = search("playwright", loader=loader, db_path=db, limit=5)
    assert any("a" in r.get("name", "") for r in results)


# ---------------------------------------------------------------------------
# R33 — added algorithmic-art, canvas-design, brand-guidelines
# ---------------------------------------------------------------------------


def test_skip_set_excludes_only_internal_comms_and_web_artifacts():
    """R33: SKIP should contain exactly the two skills that are
    not useful for a coding agent (corporate comms + Claude.ai
    artifacts-only)."""
    from scripts.adapt_anthropic_skills import SKIP
    # Must contain (R31 baseline)
    assert "internal-comms" in SKIP
    assert "web-artifacts-builder" in SKIP
    # Must NOT contain (R33 added these)
    assert "brand-guidelines" not in SKIP
    assert "algorithmic-art" not in SKIP
    assert "canvas-design" not in SKIP


def test_r33_new_skills_in_production():
    """The 3 R33 skills must be in the production kairos/skills/ dir.

    R38.11: they were shipped twice — a flat ``anthropic__<name>.md`` carrying the
    trigger metadata plus a ``<name>/SKILL.md`` carrying the body — and the loader
    kept only one of them by name. scripts/merge_duplicate_skills.py folded the
    metadata into the directory copy and removed the duplicate, so the canonical
    location is now the directory. The assertions are unchanged: priority, a
    ``when:`` trigger and the provenance line all still have to be there.
    """
    from pathlib import Path
    kairos_skills = Path(__file__).resolve().parent.parent / "kairos" / "skills"
    for name in ("algorithmic-art", "canvas-design", "brand-guidelines"):
        p = kairos_skills / name / "SKILL.md"
        assert p.exists(), f"{p} should exist after R33 adaptation"
        text = p.read_text(encoding="utf-8")
        assert "priority: 0.7" in text
        assert "when:" in text
        assert "Adapted from anthropics/skills" in text
        # And the duplicate is gone for good.
        assert not (kairos_skills / f"anthropic__{name}.md").exists()


def test_r33_skills_discoverable_by_loader():
    """SkillsLoader.discover() must return the 3 R33 skills."""
    from kairos.skills import SkillsLoader
    loader = SkillsLoader()
    names = {s.name for s in loader.discover()}
    for n in ("algorithmic-art", "canvas-design", "brand-guidelines"):
        assert n in names, f"{n} should be discoverable"


def test_brand_guidelines_has_color_palette():
    """The brand-guidelines body must contain the canonical color hexes."""
    from pathlib import Path
    p = Path("kairos/skills/anthropic__brand-guidelines.md")
    if not p.exists():
        pytest.skip("brand-guidelines not adapted (skip first)")
    text = p.read_text(encoding="utf-8")
    # Spot-check the canonical Anthropic brand colors
    assert "#141413" in text  # dark
    assert "#faf9f5" in text  # light
    assert "#d97757" in text  # orange accent
    assert "Poppins" in text  # heading font
    assert "Lora" in text     # body font


def test_algorithmic_art_mentions_p5js():
    """algorithmic-art body should reference p5.js + seeded randomness."""
    from pathlib import Path
    p = Path("kairos/skills/anthropic__algorithmic-art.md")
    if not p.exists():
        pytest.skip("algorithmic-art not adapted")
    text = p.read_text(encoding="utf-8")
    assert "p5.js" in text or "p5js" in text.lower()
    assert "randomSeed" in text or "seed" in text.lower()
