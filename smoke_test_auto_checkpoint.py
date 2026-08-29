"""Smoke test for R38.6 §30 auto-checkpoint."""
import shutil
import tempfile
from pathlib import Path

from kairos.auto_checkpoint import AutoCheckpointer


def test_before_write_creates_snapshot():
    tmp = Path(tempfile.mkdtemp(prefix="kairos_smoke_"))
    try:
        ck = AutoCheckpointer(project_dir=tmp)
        # Create a file
        target = tmp / "foo.txt"
        target.write_text("v1\n", encoding="utf-8")

        # before_write takes rel path
        snap_rel = ck.before_write("foo.txt")
        assert snap_rel is None, f"unexpected return: {snap_rel}"
        snaps = ck.list_snapshots()
        assert len(snaps) == 1, f"expected 1 snapshot, got {snaps}"
        # Read manifest backup
        snap_root = tmp / ".kairos" / "autocheckpoints"
        ts_dirs = sorted([p for p in snap_root.iterdir() if p.is_dir()])
        assert len(ts_dirs) == 1
        snap_file = ts_dirs[0] / "foo.txt"
        assert snap_file.exists(), f"snap file {snap_file} should exist"
        assert snap_file.read_text(encoding="utf-8") == "v1\n"

        # 2nd snapshot
        target.write_text("v2\n", encoding="utf-8")
        ck.before_write("foo.txt")
        snaps2 = ck.list_snapshots()
        assert len(snaps2) == 2, f"expected 2 snapshots, got {snaps2}"

        # prune
        ck.prune(max_keep=1)
        snaps3 = ck.list_snapshots()
        assert len(snaps3) == 1, f"prune should keep 1, got {snaps3}"

        # New file (no snapshot, no error)
        new = tmp / "new.txt"
        new.write_text("fresh", encoding="utf-8")
        result = ck.before_write("new.txt")
        # Hmm — file exists, so it WILL be snapshotted. Test a real new file instead.
        ghost = tmp / "ghost.txt"
        result = ck.before_write("ghost.txt")
        assert result is None, f"expected None for non-existent file, got {result}"
        snaps4 = ck.list_snapshots()
        # 1 (from prune) + 0 (existing new.txt got snapshotted, so 2 actually) + 0 (ghost)
        # We expect 1 from prune + 1 from new.txt write = 2
        assert len(snaps4) == 2, f"unexpected count: {snaps4}"

        # Path traversal rejected
        result = ck.before_write("../outside.txt")
        assert result == "path traversal rejected", f"expected rejection, got {result}"

        print("ALL SMOKE CHECKS PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_before_write_creates_snapshot()
