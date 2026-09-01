"""Skills framework.

Mirrors the cloud task Harness's Skills pattern: small, focused Markdown files
with YAML frontmatter that get loaded into the system_prompt when the
current task context matches.

Two scopes, both scanned, project wins on name collision:
- Global:  ~/.kairos/skills/*.md
- Project: <project.work_dir>/.kairos/skills/*.md

Frontmatter format (no external lib — we just split on `---`):

    ---
    name: react-hooks
    description: React 18 hooks 最佳实践
    when:
      keyword: react
      tools: [file_write]
      globs: ["*.tsx", "*.jsx"]
    priority: 0.7
    ---

    # Skill body (Markdown)

Matching is a simple union of conditions. A skill matches when ANY
of its `when` clauses (if any) is satisfied. Higher `priority` wins.
At most `max_active` skills are injected into a single run.

This is intentionally simpler than the cloud task's full Skills spec (no
sub-agent discovery, no MCP-injected skills). We focus on the single
job AGENTS.md+Skills are great at: putting project context in front
of the model without re-asking the user.
"""
from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_MAX_ACTIVE = 3
DEFAULT_MAX_BODY_BYTES = 16384  # 16KB — long enough for battle-tested skills
                                  # (e.g. the community skill library). Priority is still
                                  # the top-N gate so total injected bytes
                                  # stay bounded.

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass
class Skill:
    """Parsed skill file."""
    name: str
    description: str = ""
    when: Dict[str, Any] = field(default_factory=dict)
    priority: float = 0.5
    body: str = ""
    source_path: Optional[Path] = None

    def matches(self, context: Dict[str, Any]) -> bool:
        """Return True if this skill applies to the given context.

        The context is expected to be flat string keys. Supported
        `when` filters:
            keyword: str | list[str]   — substr match against context
                                        fields (task title, description,
                                        message content)
            tools:   list[str]          — any current/next tool name
            globs:   list[str]          — fnmatch against context
                                        `filename`/`path` fields
        A skill with no `when` clause always matches.
        """
        if not self.when:
            return True
        for key, expected in self.when.items():
            if key == "keyword":
                needles = expected if isinstance(expected, list) else [expected]
                haystack = " ".join(
                    str(v) for v in context.values() if isinstance(v, str)
                ).lower()
                if not any(str(n).lower() in haystack for n in needles):
                    return False
            elif key == "tools":
                tools = expected if isinstance(expected, list) else [expected]
                cur_tools = context.get("tools") or []
                if not any(t in cur_tools for t in tools):
                    return False
            elif key == "globs":
                globs = expected if isinstance(expected, list) else [expected]
                path = (
                    context.get("filename")
                    or context.get("path")
                    or ""
                )
                if not any(fnmatch.fnmatch(str(path), g) for g in globs):
                    return False
            else:
                # Unknown key — be lenient and accept (don't fail-closed
                # for new fields the user invents).
                continue
        return True


def _parse_skill(path: Path) -> Optional[Skill]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("skill: failed to read %s: %s", path, exc)
        return None
    m = _FRONTMATTER_RE.search(raw)
    if not m:
        logger.warning("skill: %s has no frontmatter; skipping", path)
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        logger.warning("skill: bad frontmatter YAML in %s: %s", path, exc)
        return None
    body = m.group(2)
    if len(body.encode("utf-8")) > DEFAULT_MAX_BODY_BYTES:
        body = body.encode("utf-8")[:DEFAULT_MAX_BODY_BYTES].decode(
            "utf-8", errors="replace"
        ) + "\n\n<!-- skill body truncated -->"
    return Skill(
        name=str(meta.get("name") or path.stem),
        description=str(meta.get("description") or ""),
        when=meta.get("when") or {},
        priority=float(meta.get("priority") or 0.5),
        body=body.strip(),
        source_path=path,
    )


class SkillsLoader:
    """Discover and match skills from disk.

    Three scopes, in increasing priority order:
    - **bundled**: ``<package>/skills/*.md`` (ships with the Kairos
      install — battle-tested community skills like
      the community skill library). Last to be overridden, but
      the loader picks them up first so the user sees them
      immediately even with an empty ``~/.kairos/skills``.
    - **global**:  ``~/.kairos/skills/*.md`` (user-global)
    - **project**: ``<project.work_dir>/.kairos/skills/*.md``

    Higher priority wins on name collision (project > global > bundled).
    """

    # Sentinel for "explicitly skip the bundled scope" (vs the default
    # of "use the package's bundled dir"). Plain `None` historically
    # meant "use default"; we keep that behavior but expose this
    # sentinel so tests / tools can opt out.
    _SKIP_BUNDLED = object()

    def __init__(
        self,
        project_dir: Optional[Path] = None,
        global_dir: Optional[Path] = None,
        bundled_dir: Optional[Path] = None,
        max_active: int = DEFAULT_MAX_ACTIVE,
    ):
        self.project_dir = Path(project_dir) if project_dir else None
        self.global_dir = (
            Path(global_dir) if global_dir
            else (Path.home() / ".kairos" / "skills")
        )
        # Default bundled dir: <package>/skills/ next to skills.py.
        # Pass `SkillsLoader._SKIP_BUNDLED` to skip the bundled scope.
        if bundled_dir is None:
            bundled_dir = Path(__file__).parent / "skills"
        if bundled_dir is SkillsLoader._SKIP_BUNDLED:
            self.bundled_dir = None
        else:
            self.bundled_dir = Path(bundled_dir) if bundled_dir else None
        self.max_active = max_active

    def discover(self) -> List[Skill]:
        """Return all skills found across all three scopes.

        Discovery is **recursive**: in a monorepo, every service
        can ship its own ``.kairos/skills/<service>/<name>.md`` and
        the loader will pick it up. Nested skill names are
        namespaced with ``__`` to avoid collisions — ``backend/deploy.md``
        becomes the skill name ``backend__deploy`` (matching Claude
        Code 2.1's nested-skill loading convention).

        Scope order: bundled (lowest) → global → project (highest).
        Later scopes override earlier ones on name collision.
        """
        scopes: List[Tuple[Optional[Path], int]] = []
        if self.bundled_dir and self.bundled_dir.exists():
            scopes.append((self.bundled_dir, 0))  # lowest priority
        if self.global_dir and self.global_dir.exists():
            scopes.append((self.global_dir, 1))
        if self.project_dir:
            project_skills = self.project_dir / ".kairos" / "skills"
            if project_skills.exists():
                scopes.append((project_skills, 2))  # highest

        skills: Dict[str, Skill] = {}
        for scope_root, _scope_idx in scopes:
            for md in sorted(scope_root.rglob("*.md")):
                s = _parse_skill(md)
                if not s:
                    continue
                # Compute namespaced name from the relative path
                # inside the skills root, e.g.
                # "backend/api/commit.md" -> "backend__api__commit"
                rel = md.relative_to(scope_root)
                parts = list(rel.parts[:-1])  # drop the filename
                if parts:
                    prefix = "__".join(
                        p.replace("__", "_") for p in parts
                    )
                    namespaced = f"{prefix}__{s.name}"
                else:
                    namespaced = s.name
                # Later (higher-priority) scope wins on name collision
                # — simply overwrite.
                s.name = namespaced
                skills[namespaced] = s
        return list(skills.values())

    def match(self, context: Dict[str, Any]) -> List[Skill]:
        """Return the top-N matching skills, sorted by priority desc."""
        all_skills = self.discover()
        matched = [s for s in all_skills if s.matches(context)]
        matched.sort(key=lambda s: s.priority, reverse=True)
        return matched[: self.max_active]

    def render(self, skills: List[Skill]) -> str:
        """Render matched skills as a Markdown block for the system_prompt."""
        if not skills:
            return ""
        out: List[str] = ["# Active Skills"]
        for s in skills:
            tag = (
                f" (source: {s.source_path})" if s.source_path else ""
            )
            out.append(f"\n## {s.name}{tag}")
            if s.description:
                out.append(f"\n_{s.description}_\n")
            out.append(s.body)
        return "\n".join(out).strip()

    def for_context(
        self, context: Dict[str, Any]
    ) -> str:
        """One-shot: match + render."""
        return self.render(self.match(context))
