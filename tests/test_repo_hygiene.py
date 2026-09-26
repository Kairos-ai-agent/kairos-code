"""Repo hygiene: no tracked file may contain a maintainer's machine path.

This is a regression guard, not a style nit. Open-sourcing went out with a
Windows-only build script that pinned `D:\\software_bak\\Kairos_code` — a drive
that does not exist for anyone else — and a public doc that told readers to
`cd` into the same place. Both looked harmless locally and were wrong for every
user. If this test fails, replace the path with a relative one, `<repo>`, or an
environment variable; do not add the offending file to an allowlist.

Deliberate examples are fine: `C:\\Users\\<name>\\...`, `C:\\\\Windows\\\\System32`
(an attack path being illustrated) and Windows drive documentation.
"""
from __future__ import annotations

import getpass
import re
import subprocess
from pathlib import Path

# Markers that only ever appear on the machine this project was developed on.
# The maintainer's account name is discovered at run time. Writing it down here
# would put the very string this gate exists to catch into the public tree — and
# because this file is exempt from its own scan, nothing else would have caught
# it. An empty account name disables that one pattern rather than matching
# everything, because `(?!)` cannot match.
_ACCOUNT = getpass.getuser() or ""

FORBIDDEN = [
    r"D_bak",
    r"software_bak",
    re.escape(_ACCOUNT) if _ACCOUNT else r"(?!)",
    "[A-Z]:" + re.escape("\\Users\\") + (re.escape(_ACCOUNT) if _ACCOUNT else r"(?!)"),
    "/c/Users/" + (re.escape(_ACCOUNT) if _ACCOUNT else r"(?!)"),
]

# A line is exempt only when it is about the rule: it names a placeholder, a
# well-known path that belongs to nobody, or this gate.
#
# Comment markers were on this list, and that was the hole. `^#` waived every
# comment, so a bundled skill could name the maintainer in a note about how to
# redact names and still pass everywhere -- green locally, green in CI, and
# shipped inside the release binary. Write the example with a placeholder;
# never re-add a comment waiver so a line can pass.
ALLOW = re.compile(
    r"<name>|<user>|C:\\\\Users\\\\me|D:\\\\some\\\\machine|"
    r"C:\\\\proj|System32|/etc/(passwd|shadow)|test_repo_hygiene|"
    r"FORBIDDEN|ALLOW"
)


def tracked_text_files() -> list[Path]:
    this_file = Path(__file__).name
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                         errors="replace").stdout
    files = []
    for name in out.splitlines():
        path = Path(name)
        if path.name == this_file:
            continue  # this file defines the patterns, so it is the one file allowed to name them
        if not path.is_file():
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2",
                                   ".ttf", ".zip", ".gz", ".whl", ".pdf", ".lock"}:
            continue
        files.append(path)
    return files


def test_no_machine_paths_in_tracked_files() -> None:
    files = tracked_text_files()
    # A hygiene check that scans nothing always passes. That is not hypothetical:
    # this guard reported success in CI while failing on the same commit locally,
    # and an empty `git ls-files` (or a broken working directory) is the only way
    # this scan can produce a false pass.
    assert len(files) > 100, (
        f"only {len(files)} tracked files were scanned — `git ls-files` did not "
        "return the repository contents, so this guard would pass vacuously"
    )
    offenders: list[str] = []
    patterns = [re.compile(p) for p in FORBIDDEN]
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # binary-ish; nothing to leak here
        for lineno, line in enumerate(text.splitlines(), 1):
            if ALLOW.search(line):
                continue
            for pattern in patterns:
                if pattern.search(line):
                    offenders.append(f"{path}:{lineno}: {line.strip()[:110]}")
                    break
    assert not offenders, (
        "machine-specific paths found in tracked files:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse a relative path, <repo>, or an environment variable instead."
    )


def test_gitignore_does_not_hide_source_files() -> None:
    """A broad ignore rule once kept an i18n fragment (298 keys) out of the repo.

    Every clean clone then failed the i18n gate while the maintainer's working
    tree, where the file still existed on disk, stayed green.
    """
    required = [
        "web/src/i18n/parts/settings.json",
        "web/src/i18n/languages.json",
        "web/package-lock.json",
    ]
    missing = [p for p in required if not Path(p).is_file()]
    assert not missing, f"expected files are absent from the checkout: {missing}"

    tracked = set(subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                                 errors="replace").stdout.split())
    untracked = [p for p in required if p not in tracked]
    assert not untracked, (
        f"these files exist locally but are NOT tracked (a broad .gitignore rule?): {untracked}"
    )

def test_a_comment_line_is_not_waived() -> None:
    """The hole that let a leak ship: `^#` exempted every comment.

    A bundled skill named the maintainer in a note about how to redact names, and
    every gate stayed green -- locally, in CI, and inside the release binary. Pin
    it with the pattern list itself so this file still carries no literal.
    """
    name = FORBIDDEN[2]
    for line in (f"# note: {name} ==> user", f"#   indented {name}",
                 f"// {name} in a comment", f"    {name} bare"):
        assert re.search(r"|".join(FORBIDDEN), line), line
        assert not ALLOW.search(line), f"a comment must not be exempt: {line!r}"

