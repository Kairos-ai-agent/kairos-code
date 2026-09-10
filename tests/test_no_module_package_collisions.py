"""Guard: no ``kairos`` module may be shadowed by a same-named package.

A ``kairos/sandbox.py`` module once coexisted with an (empty)
``kairos/sandbox/__init__.py`` package. Python prefers the package, so
``import kairos.sandbox`` silently loaded the empty package — which quietly
disabled the sandbox wiring in the terminal tool and broke several importers
and test files.

This test asserts that no name inside the ``kairos`` package exists as BOTH a
``<name>.py`` module and a regular ``<name>/__init__.py`` package.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import kairos


def _collisions_in(directory: Path) -> List[str]:
    """Return human-readable collisions inside one directory."""
    found: List[str] = []
    if not directory.is_dir():
        return found
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        pkg_init = child / "__init__.py"
        sibling_module = directory / f"{child.name}.py"
        if pkg_init.exists() and sibling_module.exists():
            found.append(
                f"{directory.name}/{child.name}: both "
                f"{child.name}.py and {child.name}/__init__.py exist"
            )
    return found


def _iter_package_dirs(root: Path):
    """Yield ``root`` and every subdirectory that is a package."""
    yield root
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "__init__.py").exists():
            yield child


def test_no_module_package_name_collisions():
    root = Path(kairos.__file__).resolve().parent
    collisions: List[str] = []
    for pkg_dir in _iter_package_dirs(root):
        collisions.extend(_collisions_in(pkg_dir))
    assert not collisions, (
        "module/package name collisions shadow modules (a package wins over "
        "a same-named .py module):\n  " + "\n  ".join(collisions)
    )
