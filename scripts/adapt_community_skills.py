"""Adapt alirezarezvani/claude-skills SKILL.md files to the Kairos skill format.

Source : vendor/_oss/claude-skills/engineering-team/skills/<name>/SKILL.md
Target : kairos/skills/community__<name>.md

Adaptation:
  - Add `priority: 0.6` (community/3rd-party skills; lower than
    superpowers 0.8 and anthropic 0.7 because they're not as
    battle-tested as the upstream superpowers lib, but still above
    user ad-hoc 0.5)
  - Synthesize a `when:` block from the description
  - Add a provenance footer citing the upstream source

Same parsing rules as the R31/R33 adapters (line-by-line frontmatter
parse to survive descriptions with colons).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "vendor" / "_oss" / "claude-skills" / "engineering-team" / "skills"
DST = REPO_ROOT / "kairos" / "skills"

DEFAULT_PRIORITY = 0.6
PREFIX = "community__"

# Same keyword extractor as the other adapters
_KW_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")

STOP = {
    "the", "and", "for", "with", "any", "you", "your", "this",
    "that", "from", "when", "use", "before", "after", "into",
    "out", "all", "are", "was", "were", "been", "have", "has",
    "had", "will", "would", "could", "should", "may", "might",
    "must", "shall", "can", "not", "but", "yet", "so", "or",
    "via", "ask", "user", "users", "their", "they", "them",
    "how", "what", "why", "which", "where", "when",
}


def extract_keywords(description: str, max_kw: int = 6) -> list[str]:
    """Pick salient words from the description."""
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
    """Adapt a single community SKILL.md to Kairos format."""
    raw = src_md.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", raw, re.DOTALL)
    if not m:
        raise ValueError(f"no frontmatter in {src_md}")
    name = src_md.parent.name
    description = ""
    for line in m.group(1).splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip().strip('"').strip("'")
    body = m.group(2).rstrip() + "\n"

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

    provenance = (
        f"\n\n<!--\n"
        f"Adapted from alirezarezvani/claude-skills (MIT).\n"
        f"Upstream: https://github.com/alirezarezvani/claude-skills/tree/main/engineering-team/skills/{name}\n"
        f"Adaptation: scripts/adapt_community_skills.py\n"
        f"-->\n"
    )

    if "Adapted from alirezarezvani/claude-skills" not in body:
        body = body + provenance

    dst_md.parent.mkdir(parents=True, exist_ok=True)
    dst_md.write_text(new_fm + body, encoding="utf-8")
    return name, kws


def main() -> int:
    if not SRC.exists():
        print(
            f"ERROR: claude-skills source not found at {SRC}.\n"
            f"Run: git clone --depth=1 --filter=blob:none --sparse "
            f"https://github.com/alirezarezvani/claude-skills.git vendor/_oss/claude-skills\n"
            f"     git -C vendor/_oss/claude-skills sparse-checkout set engineering-team/skills",
            file=sys.stderr,
        )
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    adapted = 0
    for skill_dir in sorted(SRC.iterdir()):
        if not skill_dir.is_dir():
            continue
        src_md = skill_dir / "SKILL.md"
        if not src_md.exists():
            continue
        dst_md = DST / f"{PREFIX}{skill_dir.name}.md"
        try:
            name, kws = adapt_skill(src_md, dst_md)
        except Exception as exc:
            print(f"  ! {skill_dir.name}: {exc}", file=sys.stderr)
            continue
        print(f"  + community__{name}.md  keywords={kws}")
        adapted += 1
    print(f"\nAdapted {adapted} skills -> {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
