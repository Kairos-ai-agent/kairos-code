"""Single source of truth for auto-detecting a project's test command.

Two callers used to detect this independently and drifted apart:

* ``kairos.loop.precheck._auto_detect_test_command`` -- the code loop's
  precheck (runs every round).
* ``kairos.skeleton.verifiers._detect_test_command`` -- the verifier that runs
  the workspace's tests.

The skeleton copy hard-coded ``[".venv/Scripts/python.exe", "-m", "pytest",
"-q"]``, which is a *Windows-only* path: on POSIX a project with a ``.venv``
still got the ``Scripts\\python.exe`` spelling and the command failed with
``FileNotFoundError``. Both callers now delegate here so the venv path is
resolved once, and both POSIX (``bin/python``) and Windows
(``Scripts/python.exe``) layouts are understood, with a global-pytest fallback.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

#: Platform seam: the OS name the venv layout is chosen from. A module constant
#: (not a live ``os.name`` read) so a unit test can monkeypatch it to exercise
#: both layouts on one machine without mutating the real ``os.name``.
_OS_NAME: str = os.name


def venv_python(root) -> Optional[str]:
    """Relative path to the project's virtualenv interpreter, or ``None``.

    POSIX virtualenvs put the interpreter at ``.venv/bin/python`` and Windows
    at ``.venv/Scripts/python.exe``; the platform's own layout is preferred but
    the other is accepted as a fallback (a repo checked out across platforms,
    a WSL/Windows mount). The returned path is workspace-relative so a
    subprocess started with ``cwd=<root>`` resolves it on either platform.
    """
    root = Path(root)
    if _OS_NAME == "nt":
        candidates = ("Scripts/python.exe", "bin/python")
    else:
        candidates = ("bin/python", "Scripts/python.exe")
    for rel in candidates:
        if (root / ".venv" / rel).is_file():
            return ".venv/" + rel
    return None


def detect_project_test_command(
    root,
    *,
    pytest_args: Sequence[str] = ("-q",),
    prefer_venv_python: bool = False,
    require_pytest_on_path: bool = False,
    marker_files: Iterable[str] = ("pyproject.toml", "pytest.ini"),
    marker_dirs: Iterable[str] = (),
    check_npm_on_path: bool = False,
) -> Optional[List[str]]:
    """Detect a runnable test command for ``root`` (Python or Node).

    Python projects are detected by any of ``marker_files`` existing or any of
    ``marker_dirs`` being a directory. In that case:

    * ``prefer_venv_python=True`` returns ``[".venv/<...>/python", "-m",
      "pytest", *pytest_args]`` when a virtualenv is present, so the project's
      own interpreter and dependencies are used;
    * otherwise, when ``require_pytest_on_path`` is False the global
      ``pytest`` console script is assumed (the precheck's historical
      behaviour); when True it is only used if ``shutil.which("pytest")``
      finds it, otherwise ``None`` is returned.

    A ``package.json`` with a ``test`` script yields ``["npm", "test",
    "--silent"]`` (only requiring ``npm`` on PATH when ``check_npm_on_path``).
    ``None`` means "no test command detected" -- never a bare guess.
    """
    root = Path(root)
    markers_hit = any((root / m).exists() for m in marker_files) or any(
        (root / d).is_dir() for d in marker_dirs
    )
    if markers_hit:
        if prefer_venv_python:
            python = venv_python(root)
            if python is not None:
                return [python, "-m", "pytest", *pytest_args]
        if not require_pytest_on_path or shutil.which("pytest"):
            return ["pytest", *pytest_args]
        return None

    pkg = root / "package.json"
    if pkg.is_file():
        if check_npm_on_path:
            try:
                scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
            except Exception:
                return None
            if "test" in scripts and shutil.which("npm"):
                return ["npm", "test", "--silent"]
            return None
        return ["npm", "test", "--silent"]
    return None
