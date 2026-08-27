"""Adapt anthropics/skills SKILL.md files to the Kairos skill format.

Source : vendor/_oss/anthropic-skills/skills/<name>/SKILL.md
Target : kairos/skills/anthropic__<name>.md

Adaptation:
  - Add `priority: 0.7` (anthropic skills are tooling-level; lower than
    superpowers' 0.8 because they're more reference / how-to material
    than discipline-level guidance, but still above user ad-hoc 0.5)
  - Synthesize a `when:` block from the description so the loader
    can auto-inject contextually without the LLM reading every skill
  - Add a provenance footer citing the upstream source

This is a one-shot script — re-run after `git pull`ing anthropic-skills.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Resolve paths relative to repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "vendor" / "_oss" / "anthropic-skills" / "skills"
DST = REPO_ROOT / "kairos" / "skills"

# Priority for adapted skills. Lower than superpowers (0.8) because
# anthropic skills are mostly reference / how-to material; higher
# than user ad-hoc (default 0.5) so they still outrank ad-hoc.
DEFAULT_PRIORITY = 0.7

# Skills to skip (e.g. very Anthropic-specific, not useful for Kairos).
# R31: started with 6 (webapp-testing, mcp-builder, frontend-design,
#      skill-creator, theme-factory, doc-coauthoring).
# R33: added algorithmic-art, canvas-design, brand-guidelines
#      (9 skills total). Still skipping:
SKIP = {
    "internal-comms",     # Corporate comms templates, not coding
    "web-artifacts-builder",  # Claude.ai artifacts-only feature
}

# Heuristic keyword extraction (same as superpowers adapter).
_KW_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def extract_keywords(description: str, max_kw: int = 6) -> list[str]:
    """Pick salient words from the description.

    Filters out the boring stop words; returns at most ``max_kw`` words.
    """
    STOP = {
        "the", "and", "for", "with", "any", "you", "your", "this",
        "that", "from", "when", "use", "before", "after", "into",
        "out", "all", "are", "was", "were", "been", "have", "has",
        "had", "will", "would", "could", "should", "may", "might",
        "must", "shall", "can", "not", "but", "yet", "so", "or",
        "via", "ask", "use", "user", "users", "their", "they",
        "them", "how", "what", "why", "which", "where", "when",
    }
    words = _KW_RE.findall(description.lower())
    seen: list[str] = []
    for w in words:
        if w in STOP:
            continue
        if w in seen:
            continue
        seen.append(w)
        if len(seen) >= max_kw:
            break
    return seen


def adapt_skill(src_md: Path, dst_md: Path) -> tuple[str, list[str]]:
    """Adapt a single anthropic SKILL.md to Kairos format.

    Returns ``(skill_name, keywords)``. Writes to ``dst_md``.

    The Anthropic skills have minimal frontmatter (name + description
    + optional license). We preserve the body verbatim and only
    inject our own frontmatter on top.
    """
    raw = src_md.read_text(encoding="utf-8", errors="replace")
    # Find the Anthropic frontmatter
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", raw, re.DOTALL)
    if not m:
        raise ValueError(f"no frontmatter in {src_md}")
    # Parse name + description line-by-line (NOT split-on-':' on the
    # whole block — descriptions can contain colons). This is the
    # R26 lesson: a one-shot split swallowed `description: ... uses :`
    # patterns whole.
    name = src_md.parent.name
    description = ""
    for line in m.group(1).splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip()
    body = m.group(2).rstrip() + "\n"

    # Synthesize Kairos frontmatter.
    kws = extract_keywords(description)
    new_fm_lines = [
        f"name: {name}",
        f"description: {description}",
        f"priority: {DEFAULT_PRIORITY}",
    ]
    if kws:
        new_fm_lines.append("when:")
        new_fm_lines.append(f"  keyword: {kws}")
    new_fm = "---\n" + "\n".join(new_fm_lines) + "\n---\n\n"

    # Provenance footer.
    provenance = (
        f"\n\n<!--\n"
        f"Adapted from anthropics/skills (Apache-2.0).\n"
        f"Upstream: https://github.com/anthropics/skills/tree/main/skills/{name}\n"
        f"Adaptation: scripts/adapt_anthropic_skills.py\n"
        f"-->\n"
    )

    # Bail if body somehow already has provenance (idempotency)
    if "Adapted from anthropics/skills" not in body:
        body = body + provenance

    dst_md.parent.mkdir(parents=True, exist_ok=True)
    dst_md.write_text(new_fm + body, encoding="utf-8")
    return name, kws


def main() -> int:
    if not SRC.exists():
        print(
            f"ERROR: anthropic-skills source not found at {SRC}.\n"
            f"Run: git clone --depth=1 --filter=blob:none --sparse "
            f"https://github.com/anthropics/skills.git vendor/_oss/anthropic-skills\n"
            f"     git -C vendor/_oss/anthropic-skills sparse-checkout set skills/",
            file=sys.stderr,
        )
        return 1
    if not SRC.is_dir():
        print(f"ERROR: {SRC} is not a directory", file=sys.stderr)
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    adapted = 0
    skipped = 0
    for skill_dir in sorted(SRC.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name in SKIP:
            print(f"  - skip {skill_dir.name} (in SKIP list)")
            skipped += 1
            continue
        src_md = skill_dir / "SKILL.md"
        if not src_md.exists():
            print(f"  - skip {skill_dir.name} (no SKILL.md)")
            skipped += 1
            continue
        dst_md = DST / f"anthropic__{skill_dir.name}.md"
        try:
            name, kws = adapt_skill(src_md, dst_md)
        except Exception as exc:
            print(f"  ! {skill_dir.name}: {exc}", file=sys.stderr)
            continue
        print(f"  + anthropic__{name}.md  keywords={kws}")
        adapted += 1
    print(f"\nAdapted {adapted} skills, skipped {skipped} -> {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
