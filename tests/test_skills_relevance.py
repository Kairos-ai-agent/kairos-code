"""Regression tests for skill *relevance* / injection discipline.

The bug these pin: ``SkillsLoader.match()`` sorted only by ``priority``. The
first 3 no-when skills in alphabetical discovery order (``3d-storyboard-previz``
at ~16KB plus two ``a-stock-analysis`` copies) therefore filled every slot on
an unrelated prompt — ~21,232 chars of injected noise per turn — while skills
whose ``when`` clause actually matched got crowded out.

Contract now:
  1. a real ``when`` hit outranks a no-``when`` skill,
  2. then ``priority`` desc, then shorter body first,
  3. a single injected body is capped at ``DEFAULT_MAX_INJECT_BODY_CHARS``,
     with an explicit marker (name + original length), never silently,
  4. at most ``DEFAULT_MAX_ACTIVE`` skills are injected.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.skills import (
    DEFAULT_MAX_ACTIVE,
    DEFAULT_MAX_INJECT_BODY_CHARS,
    SkillsLoader,
)

_NO_BUNDLED = SkillsLoader._SKIP_BUNDLED
# A prompt with nothing to do with stock quotes or storyboards.
UNRELATED_CONTEXT = {"message": "修复 python 登录 bug"}


def _write_skill(root: Path, fname: str, text: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / fname).write_text(text, encoding="utf-8")


def _isolated_loader(tmp_path: Path, **kw) -> SkillsLoader:
    """Loader whose only skill scope is ``tmp_path/proj/.kairos/skills``."""
    proj = tmp_path / "proj"
    return SkillsLoader(
        project_dir=proj,
        global_dir=proj / "no-global",
        bundled_dir=_NO_BUNDLED,
        **kw,
    )


# ------------------------------------------------------------------
# (a) unrelated context must NOT inject the stock / storyboard skills
# ------------------------------------------------------------------

def test_unrelated_context_excludes_stock_and_storyboard_skills():
    """Uses the *real* bundled skill library (604 skills on this machine)."""
    loader = SkillsLoader(global_dir=Path("/nonexistent/kairos-global-skills"))
    matched = loader.match(UNRELATED_CONTEXT)
    names = {s.name for s in matched}

    assert "3d-storyboard-previz" not in names
    assert "a-stock-analysis" not in names
    assert "a-stock-analysis-1.0.0" not in names
    # Bounded total: the old pathology injected ~21k chars of noise.
    assert len(loader.for_context(UNRELATED_CONTEXT)) < 16000


def test_debug_prompt_surfaces_the_debugging_skill():
    """A genuinely relevant `when` skill wins the prompt."""
    loader = SkillsLoader(global_dir=Path("/nonexistent/kairos-global-skills"))
    matched = loader.match(UNRELATED_CONTEXT)
    assert matched, "expected at least one match"
    # systematic-debugging has when.keyword including 'bug'/'python'.
    assert matched[0].name == "systematic-debugging"
    assert matched[0].when  # it matched on a real clause


# ------------------------------------------------------------------
# (b) a skill whose `when` clause matches is injected
# ------------------------------------------------------------------

def test_when_hit_skill_is_injected(tmp_path):
    skills = tmp_path / "proj" / ".kairos" / "skills"
    _write_skill(
        skills,
        "login.md",
        "---\nname: login-helper\nwhen:\n  keyword: login\n---\n\nlogin body\n",
    )
    loader = _isolated_loader(tmp_path)
    matched = loader.match({"message": "fix the login flow"})
    assert [s.name for s in matched] == ["login-helper"]
    assert "login body" in loader.for_context({"message": "fix the login flow"})


# ------------------------------------------------------------------
# (c) a real when-hit outranks a no-when skill
# ------------------------------------------------------------------

def test_when_hit_outranks_no_when(tmp_path):
    skills = tmp_path / "proj" / ".kairos" / "skills"
    # The no-when skill even has a *higher* priority — the real clause wins.
    _write_skill(
        skills,
        "always.md",
        "---\nname: always-on\npriority: 0.9\n---\n\nalways body\n",
    )
    _write_skill(
        skills,
        "react.md",
        "---\nname: react-hooks\nwhen:\n  keyword: react\npriority: 0.5\n---\n\nreact body\n",
    )
    loader = _isolated_loader(tmp_path)
    matched = loader.match({"message": "build a react component"})
    assert [s.name for s in matched] == ["react-hooks", "always-on"]


def test_shorter_body_wins_tie(tmp_path):
    skills = tmp_path / "proj" / ".kairos" / "skills"
    _write_skill(
        skills, "long.md", "---\nname: long-skill\npriority: 0.5\n---\n" + "x" * 900 + "\n"
    )
    _write_skill(skills, "short.md", "---\nname: short-skill\npriority: 0.5\n---\nshort\n")
    loader = _isolated_loader(tmp_path)
    matched = loader.match({"message": "neutral"})
    assert [s.name for s in matched] == ["short-skill", "long-skill"]


# ------------------------------------------------------------------
# (d) per-skill injection cap: explicit marker + original length
# ------------------------------------------------------------------

def test_oversized_body_truncated_with_explicit_marker(tmp_path):
    original_len = DEFAULT_MAX_INJECT_BODY_CHARS + 5000
    skills = tmp_path / "proj" / ".kairos" / "skills"
    big_body = "y" * original_len
    _write_skill(
        skills,
        "big.md",
        f"---\nname: big-skill\npriority: 0.9\n---\n{big_body}\n",
    )
    loader = _isolated_loader(tmp_path)
    out = loader.for_context({"message": "neutral"})

    assert "[truncated:" in out
    assert "big-skill" in out.split("[truncated:")[1]  # marker names the skill
    assert str(original_len) in out  # marker carries the original length
    assert str(DEFAULT_MAX_INJECT_BODY_CHARS) in out
    # The injected body is really capped (body + marker), not the full text.
    assert len(out) < original_len

    # The cached body is untouched — only the injected copy is truncated.
    cached = {s.name: s for s in loader.discover()}["big-skill"]
    assert len(cached.body) >= original_len


def test_body_under_cap_has_no_marker(tmp_path):
    skills = tmp_path / "proj" / ".kairos" / "skills"
    _write_skill(
        skills, "small.md", "---\nname: small-skill\n---\n" + "z" * 1000 + "\n"
    )
    loader = _isolated_loader(tmp_path)
    out = loader.for_context({"message": "neutral"})
    assert "truncated" not in out.lower()


# ------------------------------------------------------------------
# (e) injected count bounded by MAX_ACTIVE
# ------------------------------------------------------------------

def test_default_max_active_is_five():
    assert DEFAULT_MAX_ACTIVE == 5


def test_injection_count_is_bounded(tmp_path):
    skills = tmp_path / "proj" / ".kairos" / "skills"
    for i in range(12):
        _write_skill(
            skills,
            f"s{i}.md",
            f"---\nname: skill{i}\nwhen:\n  keyword: shared\n---\nbody {i}\n",
        )
    loader = _isolated_loader(tmp_path)  # default max_active
    matched = loader.match({"message": "shared context"})
    assert len(matched) == DEFAULT_MAX_ACTIVE
    # Explicit cap still honored.
    loader2 = _isolated_loader(tmp_path, max_active=2)
    assert len(loader2.match({"message": "shared context"})) == 2
