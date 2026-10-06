"""Regression tests for file_read: truncation must never be silent.

Incident: the agent read a 20,290-byte / 528-line markdown document, got back a
13,193-char result, and reported that file_read "could not return the whole
file". Two defects had to be closed:

  1. A read that really exceeded the cap ended in a bare "(truncated)" with no
     total line count, no shown range, and no way to fetch the remainder (the
     tool exposed no offset/limit at all).
  2. The happy path must stay marker-free — no noise on a normal read.

It also pins that the *reported* incident was NOT a truncation: a CRLF + CJK
document whose byte size dwarfs its normalised char count is returned in full.
"""

import re

import pytest

from kairos.tools.cache import set_cache
from kairos.tools.file_read import FileReadTool

MARKER_RE = re.compile(
    r"\[truncated: showing lines (\d+)-(\d+) of (\d+); "
    r"call file_read with offset=(\d+) to continue\]"
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Reset the module-level tool cache so relative-path keys never collide."""
    set_cache(None)
    yield
    set_cache(None)


def _big_multiline(path, n_lines=2000, width=40):
    """Write a multi-line file comfortably over the 50k-char cap."""
    lines = [f"L{i:05d}-" + "x" * (width - 7) for i in range(n_lines)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


# --------------------------------------------------------------------------
# 3) No marker / no noise when nothing is truncated
# --------------------------------------------------------------------------

async def test_small_file_is_returned_verbatim_without_marker(tmp_workspace):
    text = "# Title\n\nhello world\nsecond line\n"
    (tmp_workspace / "note.md").write_text(text, encoding="utf-8")

    res = await FileReadTool(allowed_root=tmp_workspace).execute(path="note.md")

    assert res.success
    assert res.output == text                       # byte-for-byte, zero noise
    assert "truncated" not in res.output.lower()
    assert res.metadata["truncated"] is False
    assert res.metadata["is_full_file"] is True
    assert res.metadata["total_lines"] == 4


async def test_explicit_limit_covering_whole_file_adds_no_marker(tmp_workspace):
    lines = [f"line {i}" for i in range(1, 21)]
    (tmp_workspace / "mid.txt").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")
    tool = FileReadTool(allowed_root=tmp_workspace)

    res = await tool.execute(path="mid.txt", offset=1, limit=20)

    assert res.success
    assert "truncated" not in res.output.lower()
    assert res.metadata["is_full_file"] is True


# --------------------------------------------------------------------------
# 2) Real truncation carries an executable marker, and offset recovers the rest
# --------------------------------------------------------------------------

async def test_large_file_marker_names_lines_and_offset_recovers_rest(tmp_workspace):
    (tmp_workspace / "big.md").write_text("", encoding="utf-8")
    lines = _big_multiline(tmp_workspace / "big.md", n_lines=2000, width=40)
    tool = FileReadTool(allowed_root=tmp_workspace)

    res = await tool.execute(path="big.md")

    assert res.success
    m = MARKER_RE.search(res.output)
    assert m, f"no executable marker in:\n{res.output[-300:]!r}"
    start, end, total, nxt = (int(g) for g in m.groups())
    assert (start, total) == (1, 2000)              # correct total line count
    assert end >= 1 and nxt == end + 1

    # The text before the marker is EXACTLY the lines the marker claims.
    body = res.output[:m.start()].rstrip("\n")
    assert body.split("\n") == lines[:end]

    assert res.metadata["total_lines"] == 2000
    assert res.metadata["truncated"] is True
    assert res.metadata["shown_start_line"] == 1
    assert res.metadata["shown_end_line"] == end

    # The suggested offset really returns the next chunk (no overlap).
    res2 = await tool.execute(path="big.md", offset=nxt)
    assert res2.success
    m2 = MARKER_RE.search(res2.output)
    body2 = res2.output[:m2.start()] if m2 else res2.output
    assert body2.startswith(lines[nxt - 1])
    assert lines[nxt - 1] not in body              # page 2 is new content
    assert res2.metadata["shown_start_line"] == nxt


async def test_limit_window_marker_and_final_window(tmp_workspace):
    lines = [f"line {i}" for i in range(1, 21)]
    (tmp_workspace / "mid.txt").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")
    tool = FileReadTool(allowed_root=tmp_workspace)

    res = await tool.execute(path="mid.txt", offset=1, limit=5)
    m = MARKER_RE.search(res.output)
    assert m, res.output
    assert tuple(int(g) for g in m.groups()) == (1, 5, 20, 6)

    # Reading the final window is marked as reaching the end of the file.
    res2 = await tool.execute(path="mid.txt", offset=16, limit=5)
    assert res2.output.startswith("line 16")
    assert "showing lines 16-20 of 20; end of file" in res2.output
    assert "line 20" in res2.output
    assert res2.metadata["shown_end_line"] == 20


async def test_offset_past_eof_is_explained_not_silent(tmp_workspace):
    (tmp_workspace / "small.txt").write_text("a\nb\nc\n", encoding="utf-8")
    res = await FileReadTool(allowed_root=tmp_workspace).execute(
        path="small.txt", offset=99)

    assert res.success
    assert "past the end" in res.output
    assert "3 lines" in res.output
    assert res.metadata["offset_past_eof"] is True


# --------------------------------------------------------------------------
# 4) Other silent-loss paths: directory listing cap + non-UTF-8 decoding
# --------------------------------------------------------------------------

async def test_directory_listing_over_cap_is_marked(tmp_workspace):
    d = tmp_workspace / "many"
    d.mkdir()
    for i in range(250):
        (d / f"f{i:03d}.txt").write_text("x", encoding="utf-8")

    res = await FileReadTool(allowed_root=tmp_workspace).execute(path="many")

    assert res.success
    assert res.metadata["entry_count"] == 250
    assert res.metadata["entries_shown"] == 200
    assert res.metadata["truncated"] is True
    assert "showing 200 of 250 entries" in res.output


async def test_invalid_bytes_are_announced_not_silently_replaced(tmp_workspace):
    p = tmp_workspace / "bad.txt"
    p.write_bytes(b"good line\n\xff\xfe bad bytes \xff\nend\n")

    res = await FileReadTool(allowed_root=tmp_workspace).execute(path="bad.txt")

    assert res.success
    assert "decode notice" in res.output
    assert "not valid UTF-8" in res.output
    assert res.metadata.get("decode_replaced_chars") is True


# --------------------------------------------------------------------------
# 1) The reported incident was a false alarm: CRLF + CJK read in full
# --------------------------------------------------------------------------

async def test_crlf_cjk_file_is_read_in_full_not_truncated(tmp_workspace):
    # 528 lines, CJK-heavy, CRLF endings -> bytes >> normalised chars. The old
    # agent misread this as truncation; pin that the whole file comes back.
    line = "> 关联文档：`AI_Personal_Operating_System_软件架构设想.md`\n"
    text = line * 528
    p = tmp_workspace / "DEVELOPMENT_WORKFLOW.md"
    p.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n"))

    data = p.read_bytes()
    assert len(data) > len(data.decode("utf-8"))    # bytes visibly > chars

    res = await FileReadTool(allowed_root=tmp_workspace).execute(
        path="DEVELOPMENT_WORKFLOW.md")

    assert res.success
    assert "truncated" not in res.output.lower()
    assert res.metadata["truncated"] is False
    assert res.metadata["is_full_file"] is True
    assert res.metadata["total_lines"] == 528
    assert res.output.count("\n") == 528            # every line present


# --------------------------------------------------------------------------
# Caching must be keyed on offset/limit (a continuation must not be shadowed)
# --------------------------------------------------------------------------

async def test_cache_is_keyed_on_offset(tmp_workspace):
    (tmp_workspace / "big.md").write_text("", encoding="utf-8")
    _big_multiline(tmp_workspace / "big.md", n_lines=2000, width=40)
    tool = FileReadTool(allowed_root=tmp_workspace)

    first = await tool.execute(path="big.md")
    m = MARKER_RE.search(first.output)
    nxt = int(m.group(4))
    second = await tool.execute(path="big.md", offset=nxt)

    # If the cache ignored offset, `second` would be byte-identical to `first`.
    assert second.output != first.output
    assert second.metadata["shown_start_line"] == nxt
