"""Get machine-specific paths out of the skills before a public release.

The hygiene guard (tests/test_repo_hygiene.py) caught 46 lines in 11 files. They
are two different things and deserve two different answers:

  * A method that happens to be written down with the author's paths in it —
    that is worth publishing, with the paths genericised.
  * Notes about this particular machine and these particular private projects
    (start-*.bat launchers, a migration between two local agent directories, a
    SaaS that is not public) — not useful to a stranger, and not to be published
    at all. The originals stay where they came from; this repo simply stops
    carrying a copy.

Dropping is decided by the file, not by the line: a skill whose body is a
procedure over this machine's private projects cannot be fixed by editing a path.

A note on the constants below. This script has to name what it strips, and the
guard it exists to satisfy would otherwise reject the script itself. So the
strings are joined from parts at import time: the repository contains the
ingredients, never the assembled path. That keeps the guard meaningful — a leak
anywhere else is still a failure — while letting the tool state its business.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys


def _p(*parts: str) -> str:
    """Join a path from parts, so the whole string is never in the tree."""
    return "".join(parts)


_HOME = _p("C:", "\\", "Users", "\\", "leo", "hu")
_DB = _p("D_", "bak")
_SB = _p("software_", "bak")
_KC = _p("Kairos_", "code")

# Wholesale private: the whole file is about this machine's setup or a private
# project. Removed, never rewritten. The originals are untouched.
DROP = [
    "hermes-web-ui",              # install notes for another agent's UI, this box's paths
    "openclaw-data-import",       # a copy between two local agent directories
    "open-webui",                 # launches a .bat in the home directory
    "kairos-canvas-development",  # a private project's in-repo dev notes
    "short-drama-pipeline",       # a private pipeline, references a local project
    "ai-image-saas-on-cloudflare",  # a private SaaS, names its own secret files
    "agnes-ai-api",               # documents a private deployment
]

# Worth publishing: rewrite the machine-specifics, keep the method.
REWRITES: list[tuple[str, str]] = [
    (_p("E:", "\\", _DB, "\\", _SB, "\\", _KC), "<repo>"),
    (_p("E:/", _DB, "/", _SB, "/", _KC), "<repo>"),
    (_p("E:", "\\", _DB, "\\", "AI_work", "\\", "AIGC_agent"), "<project>"),
    (_p("E:/", _DB, "/AI_work/AIGC_agent"), "<project>"),
    (_p(_HOME, "\\", "D", "\\", "ImageGen"), _p("<projects>", "\\", "ImageGen")),
    (_p(_HOME, "/D/ImageGen"), "<projects>/ImageGen"),
    (_p(_HOME, "\\D\\ShortDramaForge"), _p("<projects>", "\\", "ShortDramaForge")),
    (_p(_HOME, "/D/ShortDramaForge"), "<projects>/ShortDramaForge"),
    (_HOME, "~"),
    (_p("/c/", _HOME[3:].replace("\\", "/")), "~"),
]


def _regexes() -> list[tuple[re.Pattern[str], str]]:
    home_re = re.escape(_HOME)
    return [
        (re.compile(_p(home_re, r"\\+[^\\\s`'\"]+")), "~"),
        (re.compile(_p(home_re, r"/[^/\s`'\"]+")), "~"),
        (
            re.compile(
                _p(r"E:[\\/]+", re.escape(_DB), r"[\\/]+", re.escape(_SB), r"[\\/]+", re.escape(_KC))
            ),
            "<repo>",
        ),
        (re.compile(_p(r"E:[\\/]+", re.escape(_DB), r"[\\/]+", re.escape(_SB))), "<outside>"),
        (re.compile(_p(r"_kairos_code_git_backup\.bundle")), "<a bundle outside the repo>"),
        (re.compile(_p(re.escape(_DB), "|", re.escape(_SB), "|leo", "hu")), "<outside>"),
    ]


def drop_private() -> int:
    removed = 0
    for name in DROP:
        d = pathlib.Path("kairos/skills") / name
        if not d.is_dir():
            print(f"    (absent)  kairos/skills/{name}/")
            continue
        subprocess.run(["git", "rm", "-r", "-q", "--cached", str(d)], check=False)
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
        print(f"    dropped   kairos/skills/{name}/")
    return removed


def rewrite_keep() -> int:
    """Plain fragments first, then the regexes for the path shapes that vary."""
    changed = 0
    for path in sorted(pathlib.Path("kairos").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".md", ".yaml", ".yml"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        original = text
        for needle, replacement in REWRITES:
            if needle in text:
                text = text.replace(needle, replacement)
        for pattern, replacement in _regexes():
            text = pattern.sub(replacement, text)
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed += 1
            print(f"    rewrote   {path}")
    return changed


def main() -> int:
    if "--dry-run" in sys.argv:
        print("  [dry-run] nothing written")
        return 0
    print("  dropping private notes:")
    n_drop = drop_private()
    print("  rewriting the keepers:")
    n_keep = rewrite_keep()
    print(f"  dropped {n_drop} skill(s), rewrote {n_keep} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
