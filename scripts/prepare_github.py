#!/usr/bin/env python
"""Point the repository at your own GitHub account before publishing.

The committed files contain the literal placeholders ``OWNER`` and ``REPO``
(pyproject URLs, README badges, the issue-template links, SECURITY.md and
CODE_OF_CONDUCT.md). This script rewrites them everywhere at once, so you do not
have to hunt them down by hand::

    python scripts/prepare_github.py --owner KairosBuilds --repo kairos-code
    python scripts/prepare_github.py --owner KairosBuilds --repo kairos-code --apply

Without ``--apply`` it only reports what it would change (dry run).
It never touches .git, node_modules, .venv, data/ or build output.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build",
             "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
             "data", "workspace", "locales", "catalog"}
TEXT_SUFFIXES = {".md", ".toml", ".yml", ".yaml", ".json", ".py", ".ts",
                 ".tsx", ".mjs", ".js", ".sh", ".bat", ".vbs", ".txt"}

# ``OWNER/REPO`` on its own (markdown links, config.yml) and inside a full URL.
PATTERNS = [
    (re.compile(r"github\.com/OWNER/REPO"), "github.com/{owner}/{repo}"),
    (re.compile(r"\bOWNER/REPO\b"), "{owner}/{repo}"),
]


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="GitHub user or org")
    ap.add_argument("--repo", default="kairos-code", help="repository name")
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    if args.owner in {"OWNER", ""} or args.repo in {"REPO", ""}:
        print("refusing to use a placeholder as the real value", file=sys.stderr)
        return 2

    hits: list[tuple[Path, int]] = []
    for path in iter_files(REPO_ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = text
        for pattern, template in PATTERNS:
            new = pattern.sub(template.format(owner=args.owner, repo=args.repo), new)
        if new != text:
            count = sum(len(p.findall(text)) for p, _ in PATTERNS)
            hits.append((path, count))
            if args.apply:
                path.write_text(new, encoding="utf-8", newline="\n")

    if not hits:
        print("nothing to do — no OWNER/REPO placeholders left")
        return 0

    verb = "updated" if args.apply else "would update"
    for path, count in hits:
        print(f"{verb}: {path.relative_to(REPO_ROOT)}  ({count} reference(s))")
    print(f"\n{len(hits)} file(s) {verb}.")
    if not args.apply:
        print("re-run with --apply to write the changes.")
    else:
        print("Now check README.md badges and pyproject [project.urls] render as expected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
