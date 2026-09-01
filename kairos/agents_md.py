"""AGENTS.md loader.

Mirrors the cloud task Harness's AGENTS.md pattern: structured Markdown files
that augment (or override) the hard-coded system_prompt in each role.

Two scopes, merged with project winning over global:
- Global:   $KAIROS_HOME/AGENTS.md  (or ~/.kairos/AGENTS.md)
- Project:  <project.work_dir>/AGENTS.md

If a project has a `.kairos/global_disabled` flag the global file is
skipped. The loader is deliberately cheap: 4KB hard cap per file, 6KB
combined cap, graceful truncation if the body gets long.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Conservative caps. AGENTS.md is injected into every LLM call's
# system_prompt, so the loader must not silently turn into a context
# bomb. Users can override via KairosAgent(agents_md_cap_bytes=...).

DEFAULT_FILE_CAP = 4096
DEFAULT_COMBINED_CAP = 6144
INTERNAL_FALLBACK_NAME = "(built-in fallback)"

_FALLBACK_BODY = """# Kairos Code — built-in guidance

This is the built-in fallback used when no AGENTS.md is found at any scope.
Override by creating `AGENTS.md` in your project root or in `~/.kairos/`.

## Coding conventions
- Prefer small, focused changes over large rewrites.
- Read before you write. Use `file_read` (or the matching code-search tool)
  to confirm the current state of a file before editing it.

## Tool usage
- `file_write` overwrites the entire file. Prefer `file_edit_replace`
  for surgical edits so the Coder's changes are reviewable in git diff.
- `terminal` runs in the project work_dir. Network egress is not blocked
  at the LLM layer; rely on the working-directory sandbox to keep the
  blast radius local.

## Review contract
- Every code change is reviewed by `Reviewer` (1 main + up to 7
  specialist reviewers). A round only counts as passing when the
  weighted average score >= 80 and no CRITICAL issues remain.
"""


@dataclass
class AgentsMdSection:
    """One `## Heading` block in an AGENTS.md file."""
    heading: str
    body: str

    def render(self) -> str:
        return f"## {self.heading}\n\n{self.body.strip()}\n"


@dataclass
class AgentsMd:
    """Parsed AGENTS.md — header + ordered list of sections."""
    source_path: Optional[Path]
    sections: List[AgentsMdSection] = field(default_factory=list)
    raw_truncated: bool = False

    def is_empty(self) -> bool:
        return not self.sections

    def render(self, cap_bytes: int = DEFAULT_COMBINED_CAP) -> str:
        out_parts: List[str] = []
        if self.source_path:
            out_parts.append(
                f"<!-- AGENTS.md: {self.source_path} -->"
            )
        for s in self.sections:
            out_parts.append(s.render())
        body = "\n".join(out_parts).strip()
        if len(body.encode("utf-8")) > cap_bytes:
            body = body.encode("utf-8")[:cap_bytes].decode(
                "utf-8", errors="replace"
            ) + "\n\n<!-- agents_md: truncated -->"
        return body

    def get_section(self, name: str) -> Optional[str]:
        """Case-insensitive lookup of a section by its `##` heading."""
        lower = name.strip().lower()
        for s in self.sections:
            if s.heading.strip().lower() == lower:
                return s.body.strip()
        return None


# Heading regex: matches `## Foo` (level 2) or `### Foo` (level 3). We
# ignore `##` only (single #) since that's the file title and would be
# noise inside the system prompt.
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)


def _parse_agents_md(text: str, source: Optional[Path]) -> AgentsMd:
    """Split a Markdown body into `##` / `###` sections."""
    sections: List[AgentsMdSection] = []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return AgentsMd(source_path=source)
    # Anything before the first heading goes into a synthetic "Overview" section.
    first = matches[0]
    pre = text[: first.start()].strip()
    if pre:
        sections.append(AgentsMdSection(heading="Overview", body=pre))
    for idx, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        sections.append(AgentsMdSection(heading=m.group(2).strip(), body=body))
    return AgentsMd(source_path=source, sections=sections)


def _read_capped(path: Path, cap: int) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("AGENTS.md: failed to read %s: %s", path, exc)
        return None
    if len(raw.encode("utf-8")) > cap:
        truncated = raw.encode("utf-8")[:cap].decode("utf-8", errors="replace")
        return truncated + "\n\n<!-- truncated -->"
    return raw


class AgentsMdLoader:
    """Loads AGENTS.md from global + project scopes, project wins."""

    def __init__(
        self,
        project_dir: Optional[Path] = None,
        global_path: Optional[Path] = None,
        file_cap_bytes: int = DEFAULT_FILE_CAP,
        combined_cap_bytes: int = DEFAULT_COMBINED_CAP,
    ):
        self.project_dir = Path(project_dir) if project_dir else None
        # Default global location: ~/.kairos/AGENTS.md. We avoid pulling
        # `settings` at import time so the loader stays testable in
        # isolation.
        self.global_path = (
            Path(global_path) if global_path
            else (Path.home() / ".kairos" / "AGENTS.md")
        )
        self.file_cap_bytes = file_cap_bytes
        self.combined_cap_bytes = combined_cap_bytes

    def _global_disabled(self) -> bool:
        if not self.project_dir:
            return False
        return (self.project_dir / ".kairos" / "global_disabled").exists()

    def load(self) -> AgentsMd:
        """Load the highest-priority AGENTS.md available.

        Priority: project > global > internal fallback.
        When both global and project exist, both are merged (project
        sections appended after global sections).
        """
        global_md: Optional[AgentsMd] = None
        if not self._global_disabled() and self.global_path.exists():
            text = _read_capped(self.global_path, self.file_cap_bytes)
            if text:
                global_md = _parse_agents_md(text, self.global_path)
        project_md: Optional[AgentsMd] = None
        if self.project_dir:
            proj_path = self.project_dir / "AGENTS.md"
            text = _read_capped(proj_path, self.file_cap_bytes)
            if text:
                project_md = _parse_agents_md(text, proj_path)
        if project_md and global_md:
            merged = AgentsMd(source_path=project_md.source_path)
            merged.sections = list(global_md.sections) + list(project_md.sections)
            return merged
        if project_md:
            return project_md
        if global_md:
            return global_md
        # Internal fallback. We don't tag it with a path so callers can
        # detect it via is_empty()/render() and prepend their own header
        # if they want a stable identifier.
        return _parse_agents_md(_FALLBACK_BODY, source=None)

    def render(self, md: Optional[AgentsMd] = None) -> str:
        if md is None:
            md = self.load()
        return md.render(cap_bytes=self.combined_cap_bytes)

    def merge_into_system_prompt(self, base_prompt: str) -> str:
        """Append AGENTS.md content to a role's hard-coded system_prompt.

        The hard-coded `base_prompt` is preserved verbatim so the role's
        identity is never overridden — AGENTS.md only augments.
        """
        md = self.load()
        if md.is_empty():
            return base_prompt
        block = self.render(md)
        return f"{base_prompt}\n\n# Project Instructions (AGENTS.md)\n\n{block}"
