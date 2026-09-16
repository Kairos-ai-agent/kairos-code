"""Get machine-specific paths out of the skills before a public release.

The hygiene guard (tests/test_repo_hygiene.py) caught 46 lines in 11 files. They
are two different things and deserve two different answers:

  * A method that happens to be written down with the author's paths in it —
    that is worth publishing, with the paths genericised.
  * Notes about this particular machine and these particular private projects
    (start-*.bat launchers, a migration between two local agent directories, a
    SaaS that is not public) — that is not useful to a stranger and should not
    be published at all. The originals stay where they came from; the public
    repo simply does not carry a copy.

Dropping is decided by the file, not the line: a skill whose body is a procedure
over this machine's private projects cannot be fixed by editing a path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

# Wholesale private: the whole file is about this machine's setup or a private
# project. Removed from the repo, never rewritten. Originals are untouched.
DROP = [
    "hermes-web-ui",              # install notes for another agent's UI, this box's paths
    "openclaw-data-import",       # a copy between two local agent directories
    "open-webui",                 # launches C:\Users\<user>\start-open-webui.bat
    "kairos-canvas-development",  # a private project's in-repo dev notes
    "short-drama-pipeline",       # a private pipeline, references a local project
    "ai-image-saas-on-cloudflare",  # a private SaaS, names its secret files
    "agnes-ai-api",               # documents a private deployment
]

# Worth publishing: rewrite the machine-specifics, keep the method.
# (path fragment, replacement) applied in order, longest first.
REWRITES: list[tuple[str, str]] = [
    (r"E:\D_bak\software_bak\Kairos_code", "<repo>"),
    ("E:/D_bak/software_bak/Kairos_code", "<repo>"),
    (r"E:\D_bak\AI_work\AIGC_agent", "<project>"),
    ("E:/D_bak/AI_work/AIGC_agent", "<project>"),
    (r"C:\Users\you\D\ImageGen", r"<projects>\ImageGen"),
    ("C:/Users/you/D/ImageGen", "<projects>/ImageGen"),
    (r"C:\Users\you\D\ShortDramaForge", r"<projects>\ShortDramaForge"),
    ("C:/Users/you", "~"),
    (r"C:\Users\you", "~"),
    ("/c/Users/you", "~"),
    ("you", "<user>"),  # any straggler; verified by the guard afterwards
]


def drop_private() -> int:
    removed = 0
    for name in DROP:
        d = pathlib.Path("kairos/skills") / name
        if not d.is_dir():
            print(f"    (absent) {name}")
            continue
        subprocess.run(["git", "rm", "-r", "-q", "--cached", str(d)], check=False)
        import shutil

        shutil.rmtree(d, ignore_errors=True)
        removed += 1
        print(f"    dropped  kairos/skills/{name}/")
    return removed


def rewrite_keep() -> int:
    """Two passes: plain fragments, then regexes for the path shapes that vary.

    The regex pass is here rather than in the shell because a double-quoted
    heredoc eats backslashes before Python ever sees them — `\\U` becomes `U`
    and `re` dies with an incomplete escape. A file does not have that problem.
    """
    import re

    regexes = [
        (re.compile(r"[A-Za-z]:\\+Users\\+[^\\\s`'\"]+"), "~"),
        (re.compile(r"[A-Za-z]:/Users/[^/\s`'\"]+"), "~"),
        (re.compile(r"/c/Users/[^/\s`'\"]+"), "~"),
        (re.compile(r"E:[\\/]+D_bak[\\/]+software_bak[\\/]+Kairos_code"), "<repo>"),
        (re.compile(r"E:[\\/]+D_bak[\\/]+software_bak"), "<outside>"),
        (re.compile(r"_kairos_code_git_backup\.bundle"), "<a bundle outside the repo>"),
        (re.compile(r"D_bak|software_bak|you"), "<outside>"),
    ]
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
        for pattern, replacement in regexes:
            text = pattern.sub(replacement, text)
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed += 1
            print(f"    rewrote  {path}")
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
