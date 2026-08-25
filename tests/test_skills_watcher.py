"""Tests for the skills hot-reload watcher."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.skills_watcher import SkillsWatcher


# ---------------------------------------------------------------------------
# Pure-function tests (no async / no real timing)
# ---------------------------------------------------------------------------


def test_diff_detects_added_file():
    old = {Path("/a"): 1.0}
    new = {Path("/a"): 1.0, Path("/b"): 2.0}
    changed = SkillsWatcher._diff(old, new)
    assert Path("/b") in changed


def test_diff_detects_removed_file():
    old = {Path("/a"): 1.0, Path("/b"): 2.0}
    new = {Path("/a"): 1.0}
    changed = SkillsWatcher._diff(old, new)
    assert Path("/b") in changed


def test_diff_detects_modified_mtime():
    old = {Path("/a"): 1.0}
    new = {Path("/a"): 2.0}
    changed = SkillsWatcher._diff(old, new)
    assert Path("/a") in changed


def test_diff_returns_empty_when_no_change():
    old = {Path("/a"): 1.0, Path("/b"): 2.0}
    new = {Path("/a"): 1.0, Path("/b"): 2.0}
    assert SkillsWatcher._diff(old, new) == set()


# ---------------------------------------------------------------------------
# Integration: real file system, real polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watcher_fires_callback_when_skill_added(tmp_path):
    """Add a skill file mid-run, expect the callback to fire."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    watcher = SkillsWatcher([skills_dir], interval_s=0.1)
    fired: list = []
    async def on_change(paths):
        fired.append(paths)
    watcher.set_callback(on_change)
    await watcher.start()
    try:
        # Give the watcher time to settle.
        await asyncio.sleep(0.2)
        (skills_dir / "fresh.md").write_text(
            "---\nname: fresh\ndescription: x\n---\n\n# body\n",
            encoding="utf-8",
        )
        # Wait for the watcher to poll and fire.
        for _ in range(50):
            if fired:
                break
            await asyncio.sleep(0.1)
        assert fired, "watcher did not fire callback for new skill"
        assert any("fresh.md" in str(p) for p in fired[0])
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_fires_callback_when_skill_modified(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    f = skills_dir / "edit.md"
    f.write_text("v1", encoding="utf-8")
    # Important: write the file *before* starting the watcher so
    # the initial scan doesn't treat it as a new file.
    watcher = SkillsWatcher([skills_dir], interval_s=0.1)
    fired: list = []
    async def on_change(paths):
        fired.append(paths)
    watcher.set_callback(on_change)
    await watcher.start()
    try:
        await asyncio.sleep(0.2)
        # Force mtime to advance. Just rewriting may not bump the
        # mtime enough on fast filesystems.
        new_mtime = time.time() + 2
        f.write_text("v2", encoding="utf-8")
        import os
        os.utime(f, (new_mtime, new_mtime))
        for _ in range(50):
            if fired:
                break
            await asyncio.sleep(0.1)
        assert fired, "watcher did not fire for edit"
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_fires_for_deletion(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    f = skills_dir / "doomed.md"
    f.write_text("hi", encoding="utf-8")
    watcher = SkillsWatcher([skills_dir], interval_s=0.1)
    fired: list = []
    async def on_change(paths):
        fired.append(paths)
    watcher.set_callback(on_change)
    await watcher.start()
    try:
        await asyncio.sleep(0.2)
        f.unlink()
        for _ in range(50):
            if fired:
                break
            await asyncio.sleep(0.1)
        assert fired
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_stop_is_idempotent(tmp_path):
    watcher = SkillsWatcher([tmp_path])
    await watcher.start()
    await watcher.stop()
    # Second stop should be a no-op, not raise.
    await watcher.stop()


async def test_watcher_stop_sync_works_without_loop(tmp_path):
    """`stop_sync` is the variant orchestrator-shutdown / tests use;
    it must work even when there's no running event loop."""
    watcher = SkillsWatcher([tmp_path])
    await watcher.start()
    # Drop out of the event loop and call stop_sync — it should
    # not raise, even though the underlying task is still cleaning up.
    watcher.stop_sync()
    # Calling it again is a no-op.
    watcher.stop_sync()


def test_watcher_stop_sync_on_unstarted_watcher(tmp_path):
    """stop_sync on a never-started watcher is a safe no-op (used by
    orchestrator _close_project_runtime when the watcher never came
    up)."""
    watcher = SkillsWatcher([tmp_path])
    watcher.stop_sync()  # should not raise


@pytest.mark.asyncio
async def test_watcher_handles_missing_directory(tmp_path):
    """A directory that doesn't exist is fine — watcher just scans
    an empty set every poll."""
    missing = tmp_path / "does-not-exist"
    watcher = SkillsWatcher([missing], interval_s=0.1)
    await watcher.start()
    try:
        await asyncio.sleep(0.3)
        # No callback needed; just shouldn't crash.
    finally:
        await watcher.stop()
