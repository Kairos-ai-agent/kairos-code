"""Recover the descriptions the import folded away.

The importer quoted every scalar to make YAML safe, but a multi-line
`description:` written as a YAML block (`description: >`) came out as the single
character `>` — so 53 imported skills shipped with no description at all. A
skill without a description is a skill the agent cannot choose to use, which is
the one thing the import was for.

Each skill records where it came from, so the original is one lookup away: read
its front matter, collapse the description to one line, write it back quoted.
Run it twice and it does nothing the second time.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

# Where each agent keeps its skills, relative to the home directory. The
# `source-path` the import wrote is already agent-relative; this turns it back
# into a real path without embedding one.
AGENT_ROOTS: dict[str, list[str]] = {
    "hermes": ["AppData", "Local", "hermes"],
    "claude-plugins-official": [".claude", "plugins", "marketplaces", "claude-plugins-official"],
    "claude": [".claude"],
    "codex": [".codex"],
    "minimax": [".minimax"],
    "agents": [".agents"],
    "openclaw": [".openclaw"],
}

BROKEN = {"", ">", "|", ">-", "|-", ">+", "|+"}


def read_description(text: str) -> str | None:
    """The description from a front-matter block, collapsed to a single line."""
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return None
    body = m.group(1)
    block = re.search(r"^description:\s*([>|][-+]?)\s*\n((?:[ \t]+.*\n?)*)", body, re.M)
    if block:
        return " ".join(block.group(2).split())
    inline = re.search(r"^description:\s*(.+)$", body, re.M)
    if not inline:
        return None
    value = inline.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def quote(value: str) -> str:
    """A single-quoted YAML scalar.

    Single quotes, not double: inside them YAML does no escape processing at all
    (only `''` means one quote), so a Windows path in a description survives
    verbatim. Double quotes would need every backslash doubled, and the one file
    that got it wrong failed to parse at all — which is how this was caught.
    """
    return "'" + value.replace("'", "''") + "'"


def needs_repair(front_matter: str) -> bool:
    """True when the description is missing, a bare block marker, or unparseable.

    Parsing is the authority here, not string matching: after the first pass a
    bad escape produced a description that *looked* fine and made the whole file
    unloadable. The loader is what matters, so ask a YAML parser.
    """
    import yaml

    try:
        data = yaml.safe_load(front_matter + "\n")
    except Exception:
        return True
    if not isinstance(data, dict):
        return True
    value = data.get("description")
    return not isinstance(value, str) or value.strip() in BROKEN


def origin_for(source_path: str, home: pathlib.Path) -> pathlib.Path | None:
    parts = source_path.split("/")
    if not parts:
        return None
    root = AGENT_ROOTS.get(parts[0])
    if root is None:
        return None
    return home.joinpath(*root, *parts[1:])


def find_by_name(name: str, home: pathlib.Path) -> pathlib.Path | None:
    """Last resort for a skill the marker cannot place: look for it by name.

    Two files recorded `source-path: imported` — the marker my relativiser writes
    when it cannot map a prefix — so the only way back is to search the agent
    directories the import searched. Recursively: the import walked the whole
    tree, and those two skills sit under a category directory, not at the top.
    """
    for parts in AGENT_ROOTS.values():
        root = home.joinpath(*parts) / "skills"
        if not root.is_dir():
            continue
        # os.walk with onerror, not rglob: some of these trees contain dangling
        # junctions (a removed skill leaves a link behind), and rglob raises
        # mid-iteration on those, aborting the whole repair.
        for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            here = pathlib.Path(dirpath)
            if here.name == name and "SKILL.md" in filenames:
                return here / "SKILL.md"
            if f"{name}.md" in filenames:
                return here / f"{name}.md"
    return None


def main() -> int:
    home = pathlib.Path.home()
    fixed, missing, skipped = 0, [], 0
    for path in sorted(pathlib.Path("kairos/skills").rglob("SKILL.md")):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
        if not m:
            continue
        if not needs_repair(m.group(1)):
            continue
        src = re.search(r"^source-path:\s*\"?([^\"\r\n]+)\"?\s*$", m.group(1), re.M)
        origin = None
        if src:
            origin = origin_for(src.group(1).strip(), home)
        if origin is None or not origin.is_file():
            origin = find_by_name(path.parent.name, home)
        if origin is None or not origin.is_file():
            missing.append(f"{path}  <- {src.group(1) if src else '(no marker)'}")
            continue
        recovered = read_description(origin.read_text(encoding="utf-8", errors="replace"))
        if not recovered:
            missing.append(f"{path}  (origin has none)")
            continue
        new_body = re.sub(
            r"^description:.*$",
            lambda _m: f"description: {quote(recovered)}",
            m.group(1),
            count=1,
            flags=re.M,
        )
        path.write_text(text.replace(m.group(1), new_body, 1), encoding="utf-8")
        fixed += 1
        if fixed <= 3:
            print(f"    {path.parent.name}: {recovered[:70]}")
    print(f"  recovered {fixed}, unlocatable {len(missing)}, no marker {skipped}")
    for line in missing[:8]:
        print(f"    ✗ {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
