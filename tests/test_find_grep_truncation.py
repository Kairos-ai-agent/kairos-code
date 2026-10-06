"""Truncation contract for the find and grep tools.

Both tools used to cap their output and say nothing about it:

* ``find`` filled ``max_results`` paths and then ``break``-ed, and its
  metadata carried only ``{"matches": <shown>}`` — a model reading 200
  entries concluded the project had 200 files.
* ``grep`` broke out of the scan at ``max_results``, so its
  ``{"files_scanned": N, "matches": M}`` reported "files seen until the
  cap", never a total.

These tests pin the fix: a capped result always carries an explicit
``[truncated: ...]`` marker naming the *real* total (and the file count for
grep); an uncapped result carries no marker at all; and metadata never
reports a capped result as complete.

``asyncio_mode = "auto"`` (pyproject) runs the async tests directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kairos.tools import find as find_mod
from kairos.tools import grep_tool as grep_mod
from kairos.tools.find import FindTool
from kairos.tools.grep_tool import GrepTool

# 3 root-level .py + 40 nested .py + 1 deeply nested .py = 44 matches for
# both '**/*.py' and a NEEDLE grep (each file holds exactly one needle line).
ROOT_FILES = 3
NESTED_FILES = 40
TOTAL_PY = ROOT_FILES + NESTED_FILES + 1
NEEDLE = "MARKER_NEEDLE"


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    for i in range(ROOT_FILES):
        (tmp_path / f"root_{i}.py").write_text(
            f"# root {i}\n{NEEDLE} = {i}\n", encoding="utf-8")
    for i in range(NESTED_FILES):
        sub = tmp_path / f"sub{i % 4}"
        sub.mkdir(exist_ok=True)
        (sub / f"file_{i:02d}.py").write_text(
            f"# file {i}\n{NEEDLE} = {i}\n", encoding="utf-8")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep_mod.py").write_text(f"{NEEDLE} = 999\n", encoding="utf-8")
    return tmp_path


def _norm(text: str) -> str:
    return text.replace("\\", "/")


# ---------------------------------------------------------------------------
# (a) small / uncapped result -> zero noise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_uncapped_has_no_marker(tree):
    r = await FindTool(allowed_root=tree).execute(pattern="root_*.py",
                                                  max_results=200)
    assert r.success, r.error
    assert "truncated" not in r.output.lower()
    assert r.metadata["truncated"] is False
    assert r.metadata["returned"] == ROOT_FILES
    assert r.metadata["total_matches"] == ROOT_FILES


@pytest.mark.asyncio
async def test_grep_uncapped_has_no_marker(tree):
    r = await GrepTool(allowed_root=tree).execute(pattern=f"{NEEDLE} = 999",
                                                  max_results=100)
    assert r.success, r.error
    assert "truncated" not in r.output.lower()
    assert r.metadata["truncated"] is False
    assert r.metadata["returned"] == 1
    assert r.metadata["total_matches"] == 1
    assert "deep_mod.py" in _norm(r.output)


# ---------------------------------------------------------------------------
# (b) find over the limit -> marker + correct total_matches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_over_limit_marks_and_reports_real_total(tree):
    r = await FindTool(allowed_root=tree).execute(pattern="**/*.py",
                                                  max_results=10)
    assert r.success, r.error
    lines = [ln for ln in r.output.splitlines() if ln]
    # 10 paths + the marker line
    assert len(lines) == 11
    assert lines[-1] == (f"[truncated: showing 10 of {TOTAL_PY} paths; "
                         f"narrow the path/glob or raise max_results]")
    assert r.metadata["truncated"] is True
    assert r.metadata["returned"] == 10
    assert r.metadata["total_matches"] == TOTAL_PY
    assert r.metadata["total_is_lower_bound"] is False


@pytest.mark.asyncio
async def test_find_lower_bound_total_when_count_ceiling_hit(tree, monkeypatch):
    # Force the counting ceiling low so the total degrades to ">=N".
    monkeypatch.setattr(find_mod, "_COUNT_CEILING", 5)
    r = await FindTool(allowed_root=tree).execute(pattern="**/*.py",
                                                  max_results=3)
    assert r.metadata["total_is_lower_bound"] is True
    assert r.metadata["total_matches"] == 5
    assert "[truncated: showing 3 of >=5 paths;" in r.output


@pytest.mark.asyncio
async def test_find_invalid_glob_is_an_error_not_a_crash(tree):
    r = await FindTool(allowed_root=tree).execute(pattern="**.py")
    assert r.success is False
    assert "invalid glob pattern" in (r.error or "")


# ---------------------------------------------------------------------------
# (c) grep over the limit -> marker + total + file count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_over_limit_marks_total_and_file_count(tree):
    r = await GrepTool(allowed_root=tree).execute(pattern=NEEDLE,
                                                  max_results=10)
    assert r.success, r.error
    lines = [ln for ln in r.output.splitlines() if ln]
    assert len(lines) == 11  # 10 matches + marker
    assert f"[truncated: showing 10 of {TOTAL_PY} matches across " in r.output
    assert "files; narrow the pattern/path or raise max_results]" in r.output
    assert r.metadata["truncated"] is True
    assert r.metadata["returned"] == 10
    # the reported total is at least what was returned, never fewer
    assert r.metadata["total_matches"] >= r.metadata["returned"]
    assert r.metadata["total_matches"] == TOTAL_PY
    assert r.metadata["files_with_matches"] == TOTAL_PY


@pytest.mark.asyncio
async def test_grep_lower_bound_total_when_count_ceiling_hit(tree, monkeypatch):
    monkeypatch.setattr(grep_mod, "_COUNT_CEILING", 5)
    r = await GrepTool(allowed_root=tree).execute(pattern=NEEDLE,
                                                  max_results=3)
    assert r.metadata["total_is_lower_bound"] is True
    assert r.metadata["total_matches"] == 5
    assert "[truncated: showing 3 of >=5 matches across 5 files;" in r.output


@pytest.mark.asyncio
async def test_grep_max_files_marks_incomplete_scan(tree):
    r = await GrepTool(allowed_root=tree).execute(pattern=NEEDLE,
                                                  max_results=100,
                                                  max_files=5)
    assert r.success, r.error
    assert r.metadata["files_truncated"] is True
    assert r.metadata["truncated"] is True
    assert "max_files=5" in r.output
    assert "more matches may exist" in r.output


# ---------------------------------------------------------------------------
# (d) '**/*.py' recurses; '*.py' stays root-only (backward compatible)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_star_recurses_into_subdirectories(tree):
    r = await FindTool(allowed_root=tree).execute(pattern="**/*.py",
                                                  max_results=500)
    assert r.success, r.error
    found = set(_norm(r.output).splitlines())
    assert "a/b/c/deep_mod.py" in found
    assert "sub0/file_00.py" in found
    assert "root_0.py" in found
    assert r.metadata["total_matches"] == TOTAL_PY
    assert r.metadata["truncated"] is False


@pytest.mark.asyncio
async def test_single_star_stays_root_level_only(tree):
    r = await FindTool(allowed_root=tree).execute(pattern="*.py",
                                                  max_results=500)
    assert r.success, r.error
    found = set(_norm(r.output).splitlines())
    assert found == {"root_0.py", "root_1.py", "root_2.py"}


# ---------------------------------------------------------------------------
# (e) at/under the limit -> metadata must not cry wolf
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_at_exact_limit_is_not_flagged(tree):
    r = await FindTool(allowed_root=tree).execute(pattern="**/*.py",
                                                  max_results=TOTAL_PY)
    assert r.metadata["truncated"] is False
    assert r.metadata["returned"] == TOTAL_PY
    assert r.metadata["total_matches"] == TOTAL_PY
    assert "[truncated" not in r.output


@pytest.mark.asyncio
async def test_grep_at_exact_limit_is_not_flagged(tree):
    r = await GrepTool(allowed_root=tree).execute(pattern=NEEDLE,
                                                  max_results=TOTAL_PY)
    assert r.metadata["truncated"] is False
    assert r.metadata["returned"] == TOTAL_PY
    assert r.metadata["total_matches"] == TOTAL_PY
    assert "[truncated" not in r.output


@pytest.mark.asyncio
async def test_grep_no_matches_metadata_is_clean(tree):
    r = await GrepTool(allowed_root=tree).execute(pattern="NO_SUCH_TOKEN_XYZ",
                                                  max_results=10)
    assert r.metadata["truncated"] is False
    assert r.metadata["returned"] == 0
    assert r.metadata["total_matches"] == 0
    assert r.output.startswith("(no matches in")


# ---------------------------------------------------------------------------
# safety boundaries: binary + oversized files are skipped, not silently matched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_skips_binary_and_oversized_files(tree):
    (tree / "blob.bin").write_bytes(b"\x00\x01" + NEEDLE.encode() + b"\x00")
    (tree / "huge.txt").write_text((NEEDLE + " big\n") * 200, encoding="utf-8")
    r = await GrepTool(allowed_root=tree).execute(pattern=NEEDLE,
                                                  max_results=500,
                                                  max_file_bytes=64)
    assert r.success, r.error
    assert r.metadata["skipped_binary_files"] >= 1
    assert r.metadata["skipped_large_files"] >= 1
    assert "blob.bin" not in _norm(r.output)
    assert "huge.txt" not in _norm(r.output)
    # the skipped files contributed no matches
    assert r.metadata["total_matches"] == TOTAL_PY
