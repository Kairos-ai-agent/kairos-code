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
of its `when` clauses (if any) is satisfied. Skills whose `when` clause
actually matched the context rank above skills with no `when` clause at
all; within a class higher `priority` wins, then shorter bodies.
At most `max_active` skills are injected into a single run, and each
injected body is capped (with an explicit marker) so one long skill
cannot dominate the block.

This is intentionally simpler than the cloud task's full Skills spec (no
sub-agent discovery, no MCP-injected skills). We focus on the single
job AGENTS.md+Skills are great at: putting project context in front
of the model without re-asking the user.
"""
from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_MAX_ACTIVE = 5
DEFAULT_MAX_BODY_BYTES = 16384  # 16KB — long enough for battle-tested skills
                                  # (e.g. the community skill library). Priority is still
                                  # the top-N gate so total injected bytes
                                  # stay bounded.
# Per-skill cap for a single injected body. A handful of 16KB narrative
# skills used to fill every slot and crowd out short, relevant ones; the
# full parsed body stays cached intact — only the *injected* copy is
# truncated, and never silently (the marker names the skill and its
# original length, mirroring file_read's truncation contract).
DEFAULT_MAX_INJECT_BODY_CHARS = 4096

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass
class Skill:
    """Parsed skill file."""
    name: str
    description: str = ""
    category: str = ""
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
        category=str(meta.get("category") or ""),
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
        # Parsed skills, keyed by file and validated on (mtime_ns, size).
        # ``discover()`` runs on *every turn* (the system prompt is rebuilt
        # once the tool list changes), and with a few hundred bundled skills
        # the read + YAML parse dominated that: measured at ~0.56s per call
        # for the 606-skill bundle, paid before the model is even asked.
        # Re-parsing only files whose mtime or size changed keeps discovery
        # correct — an edited skill is picked up immediately, with no TTL
        # window and no staleness — while skipping the expensive part.
        self._file_cache: Dict[Path, tuple] = {}

    def invalidate(self) -> None:
        """Drop cached parses (after writing skill files, if in doubt)."""
        self._file_cache.clear()

    def _parse_cached(self, path: Path) -> Optional["Skill"]:
        """Parse ``path`` unless an identical-looking parse is already cached.

        A miss costs a stat + a parse; a hit costs a stat. Anything that
        changes a file's content changes its size or its mtime (NTFS resolves
        both well below a turn), so a hit is never a stale read.
        """
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        hit = self._file_cache.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        parsed = _parse_skill(path)
        self._file_cache[path] = (key, parsed)
        return parsed

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
        seen: set = set()
        for scope_root, _scope_idx in scopes:
            for md in sorted(scope_root.rglob("*.md")):
                seen.add(md)
                s = self._parse_cached(md)
                if not s:
                    continue
                # Work on a copy: the cache hands out shared objects and the
                # namespacing below rewrites ``name``. Without the copy the
                # second discover() would namespace an already-namespaced name
                # (``backend__backend__deploy``).
                s = replace(s)
                # Compute namespaced name from the relative path
                # inside the skills root, e.g.
                # "backend/api/commit.md" -> "backend__api__commit"
                rel = md.relative_to(scope_root)
                parts = list(rel.parts[:-1])  # drop the filename
                if md.name.upper() == "SKILL.MD" and parts:
                    # Anthropic's layout is <skill-name>/SKILL.md: the directory
                    # *is* the name. Without this the skill would be called
                    # "docx__SKILL" (the filename), so `load_skill("docx")`
                    # would miss every skill installed in that layout.
                    namespaced = "__".join(
                        p.replace("__", "_") for p in parts
                    )
                elif parts:
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
        # Forget files that disappeared so a long-lived loader doesn't pin them.
        if len(self._file_cache) > len(seen):
            for stale in set(self._file_cache) - seen:
                self._file_cache.pop(stale, None)
        return list(skills.values())

    def match(self, context: Dict[str, Any]) -> List[Skill]:
        """Return the top-N matching skills.

        Ranking key, in order:
        1. **Real `when` hit first.** A skill whose `when` clause actually
           matched the context beats one that has no `when` clause and is
           only along for the ride (``matches()`` returns True for every
           no-`when` skill, and ~92% of the bundled library has none). Without
           this, a neutral prompt matched all 556 no-`when` skills, their
           identical default ``priority=0.5`` degenerated the sort to
           discovery (alphabetical) order, and the same 3 arbitrary long
           skills were injected every single turn.
        2. ``priority`` descending — explicit user intent still wins.
        3. Shorter body first — a 16KB narrative skill must not crowd out
           several short, relevant ones.
        """
        all_skills = self.discover()
        matched = [s for s in all_skills if s.matches(context)]
        matched.sort(
            key=lambda s: (1 if s.when else 0, s.priority, -len(s.body)),
            reverse=True,
        )
        return matched[: self.max_active]

    def render(
        self,
        skills: List[Skill],
        max_body_chars: int = DEFAULT_MAX_INJECT_BODY_CHARS,
    ) -> str:
        """Render matched skills as a Markdown block for the system_prompt.

        ``max_body_chars`` caps each skill's *injected* body. Oversized bodies
        are truncated with an explicit marker naming the skill and its
        original length — never silently, mirroring ``file_read``. The cached
        ``Skill.body`` is left untouched: this only affects what is rendered.
        """
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
            body = s.body
            if max_body_chars and len(body) > max_body_chars:
                original = len(body)
                body = body[:max_body_chars] + (
                    f"\n\n[truncated: skill '{s.name}' body is {original} chars; "
                    f"showing first {max_body_chars} "
                    f"(omitted {original - max_body_chars}).]"
                )
            out.append(body)
        return "\n".join(out).strip()

    def for_context(
        self, context: Dict[str, Any]
    ) -> str:
        """One-shot: match + render."""
        return self.render(self.match(context))
