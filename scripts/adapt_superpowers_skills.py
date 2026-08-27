"""Adapt obra/superpowers SKILL.md files to the Kairos skill format.

Source : vendor/_oss/superpowers/skills/<name>/SKILL.md
Target : kairos/skills/superpowers__<name>.md

Adaptation:
  - Add `priority: 0.8` (superpowers skills are discipline-level;
    should outrank user ad-hoc skills at default 0.5)
  - Synthesize a `when:` block from the description's first
    "Use when ..." clause (so the loader can auto-inject
    contextually without the LLM having to read every skill)

This is a one-shot script — re-run after `git pull`ing superpowers.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Resolve paths relative to repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "vendor" / "_oss" / "superpowers" / "skills"
DST = REPO_ROOT / "kairos" / "skills"

# Priority for adapted skills. Higher than user ad-hoc (0.5 default)
# so discipline wins, but lower than project-scope overrides (which
# overwrite on name collision).
DEFAULT_PRIORITY = 0.8

# Heuristic keyword extraction from "Use when ..." clause in the
# description. If the description is "Use when implementing any
# feature or bugfix, before writing implementation code", we pull
# ["implement", "feature", "bugfix", "implementation"].
_KW_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def extract_keywords(description: str, max_kw: int = 6) -> list[str]:
    """Pick salient words from a 'Use when ...' description.

    Filters out the boring stop words; returns at most `max_kw` words.
    """
    STOP = {
        "the", "and", "for", "with", "any", "you", "your", "this",
        "that", "from", "when", "use", "before", "after", "into",
        "out", "all", "are", "was", "were", "been", "have", "has",
        "had", "will", "would", "could", "should", "may", "might",
        "must", "shall", "can", "not", "but", "yet", "so", "or",
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
    """Adapt a single superpowers SKILL.md to Kairos format.

    Returns (skill_name, keywords). Writes to dst_md.
    """
    raw = src_md.read_text(encoding="utf-8", errors="replace")
    # Find the Anthropic frontmatter
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", raw, re.DOTALL)
    if not m:
        raise ValueError(f"no frontmatter in {src_md}")
    # Parse name + description line-by-line (NOT split-on-':' on the
    # whole block — descriptions can contain colons).
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

    # Add a provenance footer so the user knows where the skill came
    # from (matches the existing `render()` source_path annotation).
    provenance = (
        f"\n\n<!--\n"
        f"Adapted from obra/superpowers (Apache-2.0 / MIT).\n"
        f"Upstream: https://github.com/obra/superpowers/tree/main/skills/{name}\n"
        f"Adaptation: scripts/adapt_superpowers_skills.py\n"
        f"-->\n"
    )

    # Bail if body somehow already has provenance (idempotency)
    if "Adapted from obra/superpowers" not in body:
        body = body + provenance

    dst_md.parent.mkdir(parents=True, exist_ok=True)
    dst_md.write_text(new_fm + body, encoding="utf-8")
    return name, kws


def main() -> int:
    if not SRC.exists():
        print(
            f"ERROR: superpowers source not found at {SRC}.\n"
            f"Run: git clone --depth=1 --filter=blob:none --sparse "
            f"https://github.com/obra/superpowers.git vendor/_oss/superpowers\n"
            f"     git -C vendor/_oss/superpowers sparse-checkout set skills/",
            file=sys.stderr,
        )
        return 1
    if not SRC.is_dir():
        print(f"ERROR: {SRC} is not a directory", file=sys.stderr)
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    adapted = 0
    for skill_dir in sorted(SRC.iterdir()):
        src_md = skill_dir / "SKILL.md"
        if not src_md.exists():
            continue
        # Skip meta skills (e.g. using-superpowers is a pointer to others)
        dst_md = DST / f"superpowers__{skill_dir.name}.md"
        try:
            name, kws = adapt_skill(src_md, dst_md)
        except Exception as exc:
            print(f"  skip {skill_dir.name}: {exc}", file=sys.stderr)
            continue
        print(f"  + superpowers__{name}.md  keywords={kws}")
        adapted += 1
    print(f"\nAdapted {adapted} skills -> {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
