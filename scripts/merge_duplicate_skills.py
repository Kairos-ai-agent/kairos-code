"""Merge the duplicated bundled skills.

The shipped set carries each adapted skill twice: a flat ``<prefix>__x.md``
(which holds the trigger metadata — ``priority``, ``when:``, ``globs:``) and an
``x/SKILL.md`` (the original body, installed by ``scripts/install_extensions.py``).
The loader keys by name, so one silently replaced the other — and because the
directory copy parsed later, the metadata that makes a skill *fire* was the half
that got dropped. ``kairos doctor`` called it "36 skills loaded" while TDD stopped
matching a TDD prompt.

This script makes the directory copy whole — metadata carried over, front-matter
name aligned with the directory — and removes the flat duplicate. Flat skills
with no directory twin (``community__tdd-guide.md``, …) are left alone.

Idempotent: run it twice and the second run reports nothing to do.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "kairos" / "skills"

CARRY = ("priority", "when", "globs", "keywords", "tags")
sys.path.insert(0, str(REPO))


def _front_matter(path: Path) -> Tuple[Optional[str], str, str]:
    """``(front matter, body, raw text)`` — ``None`` when there is none."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None, text, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text, text
    return text[3:end], text[end + 4:], text


def _key(path: Path) -> str:
    """The name the *loader* will assign to this file — that is what collides.

    A directory skill is named after its directory; a flat file is named after
    the ``name:`` in its front matter (the filename only decides it when there
    is no front matter). Guessing here produced a migration that found nothing,
    so this mirrors ``SkillsLoader.discover`` exactly.
    """
    from kairos.skills import _parse_skill

    rel = path.relative_to(SKILLS)
    parts = list(rel.parts[:-1])
    if path.name.upper() == "SKILL.MD" and parts:
        return "__".join(parts)
    skill = _parse_skill(path)
    declared = (getattr(skill, "name", "") or path.stem) if skill else path.stem
    return "__".join(parts + [declared]) if parts else declared


def _carried_lines(flat_fm: str) -> List[str]:
    """The metadata lines worth copying, as they appear in the flat file."""
    out: List[str] = []
    current_key = ""
    for line in flat_fm.splitlines():
        stripped = line.rstrip()
        if stripped and not stripped[0].isspace():
            current_key = stripped.split(":", 1)[0].strip()
            if current_key in CARRY:
                out.append(stripped)
            continue
        # Continuation of the block we are carrying (e.g. the `keyword:` list).
        if current_key in CARRY and stripped:
            out.append(stripped)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    files = sorted(SKILLS.rglob("*.md"))
    by_name: Dict[str, List[Path]] = {}
    for p in files:
        by_name.setdefault(_key(p), []).append(p)

    merged, removed, skipped = 0, 0, 0
    for name, paths in sorted(by_name.items()):
        if len(paths) < 2:
            continue
        directory = next((p for p in paths if p.name.upper() == "SKILL.MD"), None)
        flats = [p for p in paths if p is not directory]
        if directory is None:
            skipped += 1
            continue

        dir_fm, dir_body, _ = _front_matter(directory)
        if dir_fm is None:
            print(f"  ! {name}: directory copy has no front matter; skipping")
            skipped += 1
            continue
        have = {ln.split(":", 1)[0].strip()
                for ln in dir_fm.splitlines() if ":" in ln}
        additions: List[str] = []
        for flat in flats:
            flat_fm, _body, _raw = _front_matter(flat)
            if flat_fm is None:
                continue
            for line in _carried_lines(flat_fm):
                key = line.split(":", 1)[0].strip()
                if key not in have:
                    additions.append(line)
                    have.add(key)

        header = [ln for ln in dir_fm.splitlines()]
        # Keep the declared name in step with the directory (the loader renames
        # anyway; this is so the file reads honestly on its own).
        header = [ln for ln in header
                  if not ln.strip().startswith("name:")] + [f"name: {name}"]
        new_fm = "\n".join(header + additions)

        # Attribution lives in the flat copy's *body*. Dropping it silently
        # would be a licensing problem, so it is carried across too — and it has
        # to sit after the front matter, or the file stops parsing as a skill.
        notes: List[str] = []
        for flat in flats:
            _fm, flat_body, _raw = _front_matter(flat)
            for line in flat_body.splitlines():
                if "Adapted from" in line and line.strip() not in notes:
                    notes.append(line.strip())
        prefix = "".join(f"<!-- {note} -->\n" for note in notes)
        new_text = f"---{new_fm}\n---\n{prefix}{dir_body.lstrip(chr(10))}"

        print(f"  {name}: +{len(additions)} metadata line(s), "
              f"-{len(flats)} duplicate file(s)")
        if not args.dry_run:
            directory.write_text(new_text, encoding="utf-8")
            for flat in flats:
                flat.unlink()
        merged += 1
        removed += len(flats)

    print(f"\n  merged {merged} skill(s), removed {removed} duplicate file(s), "
          f"skipped {skipped}")
    if not args.dry_run:
        remaining = sorted(SKILLS.rglob("*.md"))
        print(f"  bundled files now: {len(remaining)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
