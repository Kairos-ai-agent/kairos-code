"""Tests for the file-edit sandbox (regression: B-10 + path traversal guard)."""

import pytest

from kairos.tools.base import ToolResult
from kairos.tools.file_edit import FileEditReplaceTool, FileEditTool
from kairos.tools.file_read import FileReadTool


@pytest.mark.asyncio
async def test_file_write_and_read_round_trip(tmp_workspace):
    write_tool = FileEditTool(allowed_root=tmp_workspace)
    read_tool = FileReadTool(allowed_root=tmp_workspace)

    res = await write_tool.execute(path="hello.txt", content="hi")
    assert res.success, res.error
    assert (tmp_workspace / "hello.txt").read_text() == "hi"

    res = await read_tool.execute(path="hello.txt")
    assert res.success
    assert res.output == "hi"
    assert res.metadata["truncated"] is False


@pytest.mark.asyncio
async def test_file_read_clamps_output_and_sets_metadata(tmp_workspace):
    big = "x" * 60_000
    (tmp_workspace / "big.txt").write_text(big)

    read_tool = FileReadTool(allowed_root=tmp_workspace)
    res = await read_tool.execute(path="big.txt")

    assert res.success
    assert len(res.output) <= 50_000 + len("\n... (truncated)")
    assert "(truncated)" in res.output
    assert res.metadata["truncated"] is True
    assert res.metadata["original_length"] == 60_000
    assert res.metadata["max_length"] == 50_000


@pytest.mark.asyncio
async def test_path_traversal_is_blocked(tmp_workspace):
    write_tool = FileEditTool(allowed_root=tmp_workspace)
    # Attempt to escape via ../ — must be refused, not silently normalized.
    res = await write_tool.execute(path="../escape.txt", content="pwn")
    assert not res.success
    assert "outside" in res.error.lower()


@pytest.mark.asyncio
async def test_file_replace_replaces_single_occurrence(tmp_workspace):
    (tmp_workspace / "a.txt").write_text("foo foo foo")
    tool = FileEditReplaceTool(allowed_root=tmp_workspace)
    res = await tool.execute(path="a.txt", old_text="foo", new_text="bar")
    assert res.success
    assert (tmp_workspace / "a.txt").read_text() == "bar foo foo"


@pytest.mark.asyncio
async def test_file_replace_missing_text_fails(tmp_workspace):
    (tmp_workspace / "a.txt").write_text("hello")
    tool = FileEditReplaceTool(allowed_root=tmp_workspace)
    res = await tool.execute(path="a.txt", old_text="zzz", new_text="bar")
    assert not res.success
    assert "not found" in res.error


@pytest.mark.asyncio
async def test_file_read_on_directory_returns_listing(tmp_workspace):
    """Reading a directory used to crash with PermissionError / IsADirectory.
    Must return a listing instead so the agent can see what's there."""
    (tmp_workspace / "sub").mkdir()
    (tmp_workspace / "sub" / "a.txt").write_text("x")
    (tmp_workspace / "sub" / "b.txt").write_text("y")

    tool = FileReadTool(allowed_root=tmp_workspace)
    res = await tool.execute(path="sub")

    assert res.success
    assert res.metadata.get("is_dir") is True
    assert "a.txt" in res.output
    assert "b.txt" in res.output


@pytest.mark.asyncio
async def test_file_read_on_workspace_root_returns_listing(tmp_workspace):
    """The common agent mistake: `path='.'`. Must not blow up."""
    (tmp_workspace / "hello.txt").write_text("hi")
    tool = FileReadTool(allowed_root=tmp_workspace)
    res = await tool.execute(path=".")
    assert res.success
    assert res.metadata.get("is_dir") is True
    assert "hello.txt" in res.output