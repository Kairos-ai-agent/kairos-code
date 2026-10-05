"""The folder picker's roots row and its allow-list.

Two bugs are pinned here:

1. The picker kept a sandbox of its **own** (home + workspace) that ignored the
   global full-access switch (``KAIROS_FULL_ACCESS`` / ``settings.json:
   fullAccess``). Both of those live on ``C:`` on Windows, so a user who had
   already switched the sandbox off still saw exactly one drive letter in the
   roots row — while ``GetLogicalDrives()`` had enumerated all of them.

2. A root could be *listed* and then answer 403 when clicked: the roots filter
   keeps ancestors of an allowed root (so the picker can be seeded at ``C:\\``
   and walked down towards home), but the list gate only accepted descendants.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from api.routes import fs as fsmod


@pytest.fixture(autouse=True)
def _no_allow_roots_env(monkeypatch):
    monkeypatch.delenv("KAIROS_FS_ALLOW_ROOTS", raising=False)


# --- the explicit override keeps working -----------------------------------


def test_env_star_means_everything(monkeypatch):
    monkeypatch.setenv("KAIROS_FS_ALLOW_ROOTS", "*")
    assert fsmod._fs_allow_roots() is None


def test_env_list_is_parsed(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("KAIROS_FS_ALLOW_ROOTS", f"{a},{b}")
    assert fsmod._fs_allow_roots() == [a, b]
    assert fsmod._list_path_allowed(a / "deep") is True
    assert fsmod._list_path_allowed(tmp_path / "elsewhere") is False


# --- the global switch now reaches the picker -------------------------------


def test_full_access_opens_every_root(monkeypatch):
    monkeypatch.setattr(fsmod, "is_full_access", lambda: True)
    assert fsmod._fs_allow_roots() is None
    assert fsmod._list_path_allowed(Path.home()) is True


def test_full_access_keeps_every_scanned_drive(monkeypatch):
    """Nothing that was scanned may be filtered back out."""
    monkeypatch.setattr(fsmod, "is_full_access", lambda: True)
    scanned = fsmod._home_roots() + (
        fsmod._windows_drives() if os.name == "nt" else fsmod._posix_mount_roots()
    )
    assert scanned, "this machine reports no roots at all"
    shown = {r.path for r in asyncio.run(fsmod.list_roots())}
    for entry in scanned:
        assert entry.path in shown, f"{entry.name} was scanned but not shown"


def test_full_access_lets_every_scanned_drive_be_opened(monkeypatch):
    monkeypatch.setattr(fsmod, "is_full_access", lambda: True)
    for entry in fsmod._home_roots() + (
        fsmod._windows_drives() if os.name == "nt" else fsmod._posix_mount_roots()
    ):
        assert fsmod._list_path_allowed(Path(entry.path)) is True


# --- the default (sandboxed) policy is unchanged apart from the trap --------


def test_default_policy_is_workspace_plus_home(monkeypatch):
    monkeypatch.setattr(fsmod, "is_full_access", lambda: False)
    allowed = fsmod._fs_allow_roots()
    assert allowed is not None
    assert Path.home() in allowed
    assert fsmod._list_path_allowed(Path.home()) is True


def test_default_policy_drops_unrelated_drives(monkeypatch):
    monkeypatch.setattr(fsmod, "is_full_access", lambda: False)
    stranger = fsmod.FsRoot(
        name="Z:", path=str(Path("/nope/z")), is_dir=True, has_children=True,
    )
    assert fsmod._root_reaches_allowed(stranger) is False
    monkeypatch.setattr(fsmod, "is_full_access", lambda: True)
    assert fsmod._root_reaches_allowed(stranger) is True


def test_ancestor_of_an_allowed_root_can_be_opened(monkeypatch, tmp_path):
    """The roots row shows ancestors; clicking one must not answer 403."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAIROS_FS_ALLOW_ROOTS", str(home))
    assert fsmod._list_path_allowed(tmp_path) is True          # ancestor
    assert fsmod._list_path_allowed(home / "sub") is True       # descendant
    assert fsmod._list_path_allowed(tmp_path / "other") is False  # elsewhere


def test_every_root_in_the_row_can_be_opened(monkeypatch):
    """No traps: whatever the row offers, the list endpoint accepts."""
    monkeypatch.setattr(fsmod, "is_full_access", lambda: False)
    for entry in asyncio.run(fsmod.list_roots()):
        assert fsmod._list_path_allowed(Path(entry.path)) is True, (
            f"{entry.name} is offered in the picker but cannot be opened"
        )
