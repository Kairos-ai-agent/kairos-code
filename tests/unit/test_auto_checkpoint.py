"""Tests for kairos.auto_checkpoint (R38.6 §30)."""
import shutil
import tempfile
from pathlib import Path

import pytest

from kairos.auto_checkpoint import AutoCheckpointer, AutoSnapshot


@pytest.fixture
def project_dir():
    tmp = Path(tempfile.mkdtemp(prefix="kairos_ck_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_init_creates_root_dir(project_dir):
    ck = AutoCheckpointer(project_dir=project_dir)
    assert (project_dir / ".kairos" / "autocheckpoints").exists()


def test_before_write_no_existing_file_returns_none(project_dir):
    ck = AutoCheckpointer(project_dir=project_dir)
    # File doesn't exist → no snapshot, no error
    assert ck.before_write("ghost.txt") is None
    assert ck.list_snapshots() == []


def test_before_write_existing_file_creates_snapshot(project_dir):
    ck = AutoCheckpointer(project_dir=project_dir)
    target = project_dir / "foo.py"
    target.write_text("print('v1')\n", encoding="utf-8")
    assert ck.before_write("foo.py") is None
    snaps = ck.list_snapshots()
    assert len(snaps) == 1
    assert snaps[0].rel_path == "foo.py"
    assert snaps[0].bytes > 0


def test_multiple_writes_create_multiple_snapshots(project_dir):
    ck = AutoCheckpointer(project_dir=project_dir, max_keep=10)
    target = project_dir / "foo.py"
    for v in range(3):
        target.write_text(f"v{v}\n", encoding="utf-8")
        ck.before_write("foo.py")
    snaps = ck.list_snapshots()
    assert len(snaps) == 3


def test_prune_keeps_newest(project_dir):
    ck = AutoCheckpointer(project_dir=project_dir, max_keep=2)
    target = project_dir / "x.txt"
    for v in range(5):
        target.write_text(f"v{v}\n", encoding="utf-8")
        ck.before_write("x.txt")
    snaps = ck.list_snapshots()
    assert len(snaps) == 2


def test_path_traversal_rejected(project_dir):
    ck = AutoCheckpointer(project_dir=project_dir)
    result = ck.before_write("../outside.txt")
    assert result == "path traversal rejected"


def test_failed_snapshot_does_not_raise(project_dir, monkeypatch):
    ck = AutoCheckpointer(project_dir=project_dir)
    target = project_dir / "a.txt"
    target.write_text("hi", encoding="utf-8")
    # Patch Path.write_bytes inside the module to raise
    from pathlib import Path as P
    orig_write = P.write_bytes
    def boom(self, *a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(P, "write_bytes", boom)
    # before_write must NOT raise — it returns the error string
    result = ck.before_write("a.txt")
    assert result is not None
    assert "disk full" in str(result)
