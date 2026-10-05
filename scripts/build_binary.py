#!/usr/bin/env python
"""Build a standalone ``kairos-code`` executable for the current platform.

Supersedes the old Windows-only build script, which pinned the maintainer's
checkout directory (a drive that does not even exist any more) and was therefore
unusable by anyone else. Everything here is derived from this file's own
location, so a fresh clone works on Windows, macOS and Linux:

    python scripts/build_binary.py --out dist              # -> dist/kairos-code(.exe)
    python scripts/build_binary.py --out dist --windowed   # Windows GUI build
    python scripts/build_binary.py --out dist --console    # force a console build

The frozen app serves the bundled Web UI, so ``web/dist`` must exist first
(``cd web && npm ci && npm run build``); this script refuses to build without it
rather than silently producing a binary with no interface.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "kairos_code_launcher.py"
WEB_DIST = ROOT / "web" / "dist"

#: Files under ``web/dist`` that nothing serves. ``web/public/branding/`` holds
#: the *master* logo the icon set is regenerated from (see _build_icons.py in
#: that folder), and Vite copies the whole public dir into dist -- so the raw
#: jpeg rode along in every binary at 2.2 MB, more than any other single asset,
#: for a file no page ever requests. Staged out at build time instead of deleted
#: from the repo: the source asset stays where the icon build expects it.
BUNDLE_SKIP = ("branding/kairos-icon.jpeg",)


def _stage_web_dist():
    """Copy web/dist minus BUNDLE_SKIP; returns the dir to hand to PyInstaller.

    Returns web/dist untouched when it is absent (CI shards run this module
    without a frontend build) or when there is nothing to skip.
    """
    import shutil
    import tempfile

    if not BUNDLE_SKIP or not WEB_DIST.is_dir():
        return WEB_DIST
    out = Path(tempfile.mkdtemp(prefix="kairos-webdist-")) / "dist"
    shutil.copytree(WEB_DIST, out,
                    ignore=shutil.ignore_patterns(*(Path(p).name for p in BUNDLE_SKIP)))
    dropped = sum((WEB_DIST / p).stat().st_size for p in BUNDLE_SKIP
                  if (WEB_DIST / p).is_file())
    print(f"[build] staged  : web/dist without {len(BUNDLE_SKIP)} dead asset(s) "
          f"({dropped / 1e6:.1f} MB not shipped)")
    return out
NAME = "kairos-code"

# Packages whose submodules PyInstaller cannot see through static analysis.
HIDDEN_IMPORTS = [
    "aiosqlite",
    "openai",
    "anthropic",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "kairos.agents.base",
    "kairos.agents.roles.coder",
    "kairos.agents.roles.design",
    "kairos.agents.roles.docs",
    "kairos.agents.roles.perf",
    "kairos.agents.roles.refactor",
    "kairos.agents.roles.reviewer",
    "kairos.agents.roles.security",
    "kairos.agents.roles.test",
]

# Data the runtime reads from disk, as (source, destination-in-bundle).
DATA_DIRS = [
    (WEB_DIST, "web/dist"),
    (ROOT / "kairos" / "yamls", "kairos/yamls"),
    (ROOT / "kairos" / "agents" / "prompts", "kairos/agents/prompts"),
    # The shipped content: skills the loader reads, the extension registries the
    # API lists, and the bundled plugin that turns on the offline MCP servers.
    # PyInstaller bundles only Python by default, so a directory missing here
    # exists in the wheel and is simply absent from the binary — which is how a
    # "36 skills installed" install can report zero of them.
    (ROOT / "kairos" / "skills", "kairos/skills"),
    (ROOT / "kairos" / "extensions", "kairos/extensions"),
    (ROOT / "kairos" / "bundled_plugins", "kairos/bundled_plugins"),
    (ROOT / "vendor", "vendor"),
]


def icon_for_platform() -> Path | None:
    """Return a platform-appropriate icon, or None (PyInstaller skips it)."""
    branding = ROOT / "web" / "public" / "branding"
    candidates = (
        [branding / "favicon.ico", branding / "kairos-icon.ico"]
        if os.name == "nt"
        else [branding / "kairos.icns", branding / "favicon.icns"]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(prog="build_binary.py")
    parser.add_argument("--out", default=str(ROOT / "dist"),
                        help="output directory for the executable (default: ./dist)")
    parser.add_argument("--name", default=NAME, help=f"executable name (default: {NAME})")
    parser.add_argument("--windowed", action="store_true",
                        help="no console window (the default on Windows)")
    parser.add_argument("--console", action="store_true",
                        help="keep a console window (the default on macOS/Linux)")
    parser.add_argument("--keep-work", action="store_true",
                        help="reuse the build cache instead of --noconfirm --clean")
    args = parser.parse_args()

    if not LAUNCHER.is_file():
        print(f"error: launcher not found: {LAUNCHER}", file=sys.stderr)
        return 2
    if not (WEB_DIST / "index.html").is_file():
        print("error: web/dist/index.html is missing — the binary would ship no UI.\n"
              "       Build the frontend first:  cd web && npm ci && npm run build",
              file=sys.stderr)
        return 2

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("error: PyInstaller is not installed.  pip install pyinstaller",
              file=sys.stderr)
        return 2

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Windowed by default on Windows (double-click experience), console elsewhere.
    windowed = args.windowed or (os.name == "nt" and not args.console)

    # Purge the package's bytecode caches first. Python validates a .pyc on (mtime,
    # size) at one-second granularity, so an edit that keeps the byte length — "0.1.2"
    # to "0.1.3", say — can leave a stale .pyc that Python and PyInstaller both accept
    # without complaint, silently freezing an old module into the binary. PyInstaller's
    # own --clean does not touch these.
    import shutil as _shutil

    for _cache in [p for _d in (ROOT / "kairos", ROOT / "api", ROOT / "scripts")
                   if _d.exists() for p in _d.rglob("__pycache__")]:
        _shutil.rmtree(_cache, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
    if not args.keep_work:
        cmd.append("--clean")
    cmd += [
        "--onefile",
        "--name", args.name,
        "--distpath", str(out_dir),
        "--workpath", str(out_dir / ".work"),
        "--specpath", str(out_dir / ".spec"),
    ]
    if windowed:
        cmd.append("--windowed")
    icon = icon_for_platform()
    if icon:
        cmd += ["--icon", str(icon)]
    staged_web = _stage_web_dist()
    for src, dest in DATA_DIRS:
        if src == WEB_DIST:
            src = staged_web
        if src.is_dir():
            cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
    for module in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", module]
    cmd += [
        "--collect-submodules", "kairos",
        "--collect-submodules", "api",
        # The bundled MCP servers and the HTTP transport import ``mcp.*`` lazily,
        # so if the package is not frozen in the servers cannot start at all —
        # and the failure is invisible until you run the packaged app: the app
        # itself comes up, then each server burns its request timeout, which is
        # a 60s wait on every start and a dead MCP layer everywhere else.
        # ``mcp`` is an optional extra, so PyInstaller will not find it by
        # following imports either.
        "--collect-submodules", "mcp",
        "--collect-submodules", "mcp.server",
        "--collect-submodules", "mcp.client",
        "--collect-data", "kairos",
        # code_search's backend (kairos/tools/code_search.py) imports semble
        # lazily, and model2vec/vicinity load tokenizers and index internals
        # through paths PyInstaller's static analysis does not follow. Collecting
        # them whole is cheap insurance: without it the frozen app degrades to
        # "semble is not installed" for that one tool while the source checkout
        # keeps working — a failure that only shows up in the shipped binary.
        "--collect-all", "semble",
        "--collect-all", "model2vec",
        "--collect-all", "vicinity",
        # hf_xet is HuggingFace's Xet transfer backend, pulled in as a
        # transitive binary dependency. Do NOT ship it: its shared object
        # carries a string that matches the HuggingFace-token shape, so
        # scan_binary_secrets.py refuses to publish the artefact — which is the
        # correct call, and the fix is to ship less, not to teach the scanner an
        # exception. Nothing is lost: huggingface_hub imports hf_xet lazily and
        # falls back to plain HTTP downloads ("Xet Storage is enabled for this
        # repo, but the 'hf_xet' package is not installed").
        "--exclude-module", "hf_xet",
        # Development-only weight that PyInstaller walks in from the venv. None
        # of it is imported by the app at runtime -- checked with `pip show`
        # (Required-by) and by grepping the runtime packages for imports. pytest
        # and mypy are for developing Kairos, chardet is required by nothing at
        # all, and tkinter is not used by either the web UI or the CLI (kept as
        # insurance against a transitive import quietly dragging it back in).
        "--exclude-module", "pytest",
        "--exclude-module", "_pytest",
        "--exclude-module", "mypy",
        "--exclude-module", "chardet",
        "--exclude-module", "tkinter",
        LAUNCHER.name,
    ]

    print(f"[build] platform : {sys.platform} / {'windowed' if windowed else 'console'}")
    print(f"[build] icon     : {icon or '(none)'}")
    print(f"[build] out      : {out_dir}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode

    binary = out_dir / (f"{args.name}.exe" if os.name == "nt" else args.name)
    if not binary.is_file():
        print(f"error: PyInstaller reported success but {binary} is missing", file=sys.stderr)
        return 1
    size_mb = binary.stat().st_size / 1048576
    print(f"[build] OK       : {binary}  ({size_mb:.1f} MB)")

    # A published artefact carries no credentials, and that must not depend on
    # someone remembering to check. The build is what gets published, so the
    # refusal belongs here, next to the size it just reported.
    scan = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scan_binary_secrets.py"), str(binary)],
        cwd=str(ROOT))
    if scan.returncode != 0:
        print("error: refusing to hand over an artefact that failed the secret scan",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
