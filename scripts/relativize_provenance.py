"""Rewrite `source-path:` provenance from absolute to agent-relative.

The import recorded where each skill came from, but it recorded an absolute
path — the sort of thing tests/test_repo_hygiene.py::test_no_machine_paths_in_tracked_files
keeps out of a public repo. The provenance is worth keeping; the
machine-specifics are not. So: strip the drive and the user name, keep the
agent and the path within it.

    <home>/hermes/skills/.archive/foo/SKILL.md  ->  hermes/skills/.archive/foo/SKILL.md
    <home>/<agent>/plugins/marketplaces/claude-plugins-official/...
                                                ->  claude-plugins-official/...
"""

from __future__ import annotations

import pathlib
import re
import sys

# Longest marker first: "claude/plugins/marketplaces/claude-plugins-official"
# must win over the generic "claude/plugins".
MARKERS: list[tuple[str, str]] = [
    ("AppData/Local/hermes/skills", "hermes/skills"),
    ("hermes/skills", "hermes/skills"),
    ("claude/plugins/marketplaces/claude-plugins-official", "claude-plugins-official"),
    ("claude/plugins", "claude/plugins"),
    ("claude/skills", "claude/skills"),
    ("codex/skills", "codex/skills"),
    ("minimax/skills", "minimax/skills"),
    ("agents/skills", "agents/skills"),
    ("openclaw/skills", "openclaw/skills"),
]


def to_relative(raw: str) -> str:
    """An absolute path (as written in YAML) -> a portable, agent-relative one."""
    v = raw.strip().strip('"').strip("'")
    # The value came out of a JSON dump, so every separator is doubled in the
    # file. Collapse, then normalise to forward slashes.
    v = v.replace("\\\\", "\\").replace("\\", "/")
    for marker, replacement in MARKERS:
        i = v.find(marker)
        if i >= 0:
            return replacement + v[i + len(marker):]
    return "imported"


def is_absolute(raw: str) -> bool:
    """True for `C:\\...`, `C:/...`, `/c/...`, `/home/...` — no names needed."""
    v = raw.strip().strip('"').strip("'")
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|/[A-Za-z]/|/home/|/Users/)", v))


def main() -> int:
    dry = "--dry-run" in sys.argv
    changed: list[tuple[str, str, str]] = []
    for path in sorted(pathlib.Path("kairos").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".md", ".yaml", ".yml"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines(keepends=True)
        out, touched = [], False
        for line in lines:
            m = re.match(r'^(source-path:\s*"?)([^"\r\n]*?)("?\s*)$', line.rstrip("\n"))
            if m and is_absolute(m.group(2)):
                new_value = to_relative(m.group(2))
                if new_value != m.group(2):
                    out.append(
                        f'{m.group(1)}{new_value}{m.group(3)}'
                        + ("\n" if line.endswith("\n") else "")
                    )
                    changed.append((str(path), m.group(2), new_value))
                    touched = True
                    continue
            out.append(line)
        if touched and not dry:
            path.write_text("".join(out), encoding="utf-8")
    verb = "would rewrite" if dry else "rewrote"
    print(f"  {verb} {len(changed)} file(s)")
    for p, before, after in changed[:3]:
        print(f"    {p}")
        print(f"      - {before[:78]}")
        print(f"      + {after[:78]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
