"""Bundled skills must actually load.

Three defects hid here, all of which made the pre-installed set unreachable:

  * the API-layer wrappers in ``kairos/borrowed_features.py`` pointed
    ``bundled_dir`` at ``<data_dir>/bundled_skills`` — a path nothing ever
    populates — so all the skills the wheel ships were invisible to them;
  * those wrappers then read ``sl.skills``, an attribute the loader does not
    have (``discover()`` *returns* the list), so they found nothing even with
    the right directories;
  * a skill installed as ``<name>/SKILL.md`` (Anthropic's layout — what the
    installer writes) was named after the *file*, ``docx__SKILL``, so
    ``load_skill("docx")`` missed it.

Note on duplicates: the shipped set currently contains each adapted skill twice
— a flat ``anthropic__x.md`` and a ``x/SKILL.md`` — and the loader keys by name,
so the two collapse into one entry. The tests below count *names*, not files.
"""

from pathlib import Path

import pytest

from kairos.skills import SkillsLoader, _parse_skill

BUNDLED = Path(__file__).resolve().parent.parent / "kairos" / "skills"


def _bundled_files():
    return sorted(p for p in BUNDLED.rglob("*.md"))


def _discovered(bundled_dir=BUNDLED):
    return {s.name for s in SkillsLoader(bundled_dir=bundled_dir).discover()}


def test_the_bundled_set_is_not_empty():
    files = _bundled_files()
    # 36 after scripts/merge_duplicate_skills.py removed the 16 flat copies that
    # duplicated a directory skill. The floor is what matters, not the number.
    assert len(files) >= 30, f"only {len(files)} bundled skill files found"


@pytest.mark.parametrize("md", _bundled_files(),
                         ids=lambda p: str(p.relative_to(BUNDLED)))
def test_every_bundled_file_parses(md):
    """A file that cannot be parsed is pre-installed but unusable."""
    assert _parse_skill(md) is not None


def test_the_loader_finds_the_whole_bundled_set():
    names = _discovered()
    assert len(names) >= 30, f"only {len(names)} skills discovered"
    # One from each layout, by the name a user would type.
    assert "docx" in names, "directory layout (docx/SKILL.md) not found"
    assert "writing-plans" in names, "flat layout with front-matter name not found"


def test_a_skill_directory_is_named_after_the_directory(tmp_path):
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: something-else\ndescription: a test skill\n---\nbody\n",
        encoding="utf-8")

    names = _discovered(tmp_path / "skills")
    assert "my-skill" in names
    assert not any(n.endswith("__SKILL") for n in names), names


def test_nested_flat_files_keep_their_namespaced_names(tmp_path):
    nested = tmp_path / "skills" / "backend"
    nested.mkdir(parents=True)
    (nested / "deploy.md").write_text(
        "---\nname: deploy\ndescription: a nested flat skill\n---\nbody\n",
        encoding="utf-8")

    assert "backend__deploy" in _discovered(tmp_path / "skills")


def test_the_api_wrappers_can_see_the_bundled_skills():
    """The regression that started this: both wrappers returned nothing."""
    from kairos.borrowed_features import list_skills, load_skill

    listed = list_skills()
    assert len(listed) >= 30, f"only {len(listed)} skills reached the API layer"

    names = {s.name for s in listed}
    assert "writing-plans" in names, sorted(names)[:8]

    # And by name, through the other wrapper.
    assert load_skill("docx") is not None
    assert load_skill("no-such-skill") is None
