"""Discovery is cached per file — and cached *correctly*.

``discover()`` runs on every turn (the system prompt is rebuilt once the tool
list changes) and measured ~0.56s per call with the 606-skill bundle, paid
before the model is even asked. The cache may not buy that back with staleness,
so the invalidation rule is pinned here as well as the speed.

The trap worth a dedicated test: ``discover()`` rewrites ``skill.name`` to add
the path namespace, and a cache hands out *shared* objects. Reusing one without
copying produces ``backend__backend__deploy`` on the second call.
"""
from __future__ import annotations

import time
from pathlib import Path

import kairos.skills as skills_mod
from kairos.skills import Skill, SkillsLoader


class _TC:
    """Stand-in for a ToolCall — ``arguments`` is whatever the provider gave."""

    def __init__(self, arguments):
        self.arguments = arguments


class _Msg:
    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls


def _loader(tmp_path: Path) -> SkillsLoader:
    return SkillsLoader(
        project_dir=tmp_path,
        # Point the other two scopes somewhere that does not exist so the test
        # sees only what it wrote, never the machine's real skill dirs.
        global_dir=tmp_path / "no-global-skills",
        bundled_dir=SkillsLoader._SKIP_BUNDLED,
    )


def _write_skill(root: Path, rel: str, name: str, body: str = "body") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nname: {name}\ndescription: d\n---\n\n{body}\n", encoding="utf-8")
    return p


# --- the cache -------------------------------------------------------------

def test_second_discovery_reparses_nothing(tmp_path, monkeypatch):
    (tmp_path / ".kairos" / "skills").mkdir(parents=True)
    for i in range(5):
        _write_skill(tmp_path, f".kairos/skills/s{i}.md", f"s{i}")
    loader = _loader(tmp_path)
    assert len(loader.discover()) == 5

    calls = []
    real = skills_mod._parse_skill
    monkeypatch.setattr(
        skills_mod, "_parse_skill",
        lambda p: (calls.append(p), real(p))[1],
    )
    loader.discover()

    assert calls == [], f"re-parsed {len(calls)} unchanged file(s)"


def test_an_edited_skill_is_seen_without_invalidate(tmp_path):
    (tmp_path / ".kairos" / "skills").mkdir(parents=True)
    _write_skill(tmp_path, ".kairos/skills/deploy.md", "deploy", "first version")
    loader = _loader(tmp_path)
    assert loader.discover()[0].body == "first version"

    time.sleep(0.05)
    # Different length on purpose: some filesystems stamp mtime in coarse
    # ticks, and a test that depends on sub-tick mtime resolution is a flake.
    _write_skill(tmp_path, ".kairos/skills/deploy.md", "deploy",
                 "second version, longer")
    assert loader.discover()[0].body == "second version, longer", (
        "a cache with no invalidation window must still see the edit"
    )


def test_a_deleted_skill_disappears(tmp_path):
    (tmp_path / ".kairos" / "skills").mkdir(parents=True)
    p = _write_skill(tmp_path, ".kairos/skills/gone.md", "gone")
    _write_skill(tmp_path, ".kairos/skills/stays.md", "stays")
    loader = _loader(tmp_path)
    assert sorted(s.name for s in loader.discover()) == ["gone", "stays"]

    p.unlink()
    assert [s.name for s in loader.discover()] == ["stays"]
    assert p not in loader._file_cache, "the cache pinned a deleted file"


def test_namespacing_is_not_applied_twice(tmp_path):
    (tmp_path / ".kairos" / "skills" / "backend").mkdir(parents=True)
    _write_skill(tmp_path, ".kairos/skills/backend/deploy.md", "deploy")
    loader = _loader(tmp_path)

    assert [s.name for s in loader.discover()] == ["backend__deploy"]
    assert [s.name for s in loader.discover()] == ["backend__deploy"], (
        "the cache handed out a Skill that was already namespaced"
    )


def test_invalidate_forces_a_reparse(tmp_path, monkeypatch):
    (tmp_path / ".kairos" / "skills").mkdir(parents=True)
    _write_skill(tmp_path, ".kairos/skills/a.md", "a")
    loader = _loader(tmp_path)
    loader.discover()

    calls = []
    real = skills_mod._parse_skill
    monkeypatch.setattr(
        skills_mod, "_parse_skill",
        lambda p: (calls.append(p), real(p))[1],
    )
    loader.invalidate()
    loader.discover()

    assert len(calls) == 1


# --- globs (documented, and previously unreachable) ------------------------

def test_globs_match_filename_and_path():
    s = Skill(name="tsx", when={"globs": ["*.tsx"]})

    assert s.matches({"filename": "App.tsx"})
    assert s.matches({"path": "/repo/src/App.tsx"})
    assert not s.matches({"filename": "App.py"})
    assert not s.matches({}), "no filename/path must not match a globs clause"


def test_touched_file_reads_the_most_recent_tool_call():
    from kairos.agents.agent_parts.memory import _touched_file

    mem = [
        _Msg([_TC({"path": "old.py"})]),
        _Msg([_TC({"path": "E:/x/App.tsx"})]),
    ]
    assert _touched_file(mem) == "E:/x/App.tsx"


def test_touched_file_accepts_json_arguments_and_gives_up_cleanly():
    from kairos.agents.agent_parts.memory import _touched_file

    assert _touched_file([_Msg([_TC('{"path": "a/b.tsx"}')])]) == "a/b.tsx"
    assert _touched_file([]) == ""
    assert _touched_file([_Msg(None)]) == ""
    assert _touched_file([_Msg([_TC({"command": "ls -la"})])]) == ""
    assert _touched_file([_Msg([_TC("not json at all")])]) == ""
