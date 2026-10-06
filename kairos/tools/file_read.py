"""File Read Tool - reads file contents (sandboxed)."""

from __future__ import annotations

from typing import Any, Optional

from kairos.tools.base import BaseTool, ToolResult


class FileReadTool(BaseTool):
    """Read the contents of a file inside the project directory.

    Use this to inspect file contents before editing, or to understand
    existing code structure. Supports reading text files up to 50KB
    (larger reads are windowed and always say exactly which lines were
    shown and how to fetch the rest). Returns directory listings when
    given a folder path.

    Example usage:
        - Read a specific file: {"path": "src/main.py"}
        - Continue a long file: {"path": "src/main.py", "offset": 181}
        - Read a slice: {"path": "src/main.py", "offset": 1, "limit": 180}
        - List a directory: {"path": "src/"}
    """

    name = "file_read"
    description = (
        "Read the contents of a text file. Returns the full text content "
        "or a directory listing if given a folder path. Reads larger than "
        "50KB are windowed: the output ends with an explicit marker naming "
        "the lines shown, the file's total line count, and the exact "
        "offset= to pass to read the remainder. Supports offset/limit for "
        "line windows. Use this BEFORE editing a file to understand its "
        "current state."
    )

    # Character budget for a single read (unchanged from the historical
    # cap). The read is now line-aware: the cap trims at a line boundary
    # and the marker always says which lines were returned.
    max_length = 50_000
    # Directory listings also used to be silently cut at 200 entries.
    max_dir_entries = 200

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read (relative to project dir)",
                    },
                    "offset": {
                        "type": "integer",
                        "description": (
                            "1-indexed line to start reading from. Default 1. "
                            "When a previous read was windowed, pass the offset= "
                            "value printed in its marker to fetch the remainder."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum number of lines to return. Omit to read "
                            "through to the end of the file (subject to the "
                            "50KB cap)."
                        ),
                    },
                },
                "required": ["path"],
            },
        }

    @staticmethod
    def _coerce_int(value: Any, default: Optional[int]) -> Optional[int]:
        """Best-effort int coercion; a bad value degrades to ``default``."""
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _window_and_mark(self, content: str, offset: Any, limit: Any) -> tuple[str, dict]:
        """Turn decoded file text into the (output, metadata) pair.

        ``content`` is the whole file as normalised text (LF line endings).
        Returns the text to hand the model plus metadata. When nothing is
        cut the output is byte-for-byte ``content`` with NO marker, so a
        normal read is never polluted.
        """
        cap = self.max_length
        start = self._coerce_int(offset, 1)
        if start is None or start < 1:
            start = 1
        lim = self._coerce_int(limit, None)
        if lim is not None and lim < 0:
            lim = None

        # Split into logical lines. A trailing newline does not create a
        # phantom final line (matches how editors / ``wc -l`` count).
        body = content[:-1] if content.endswith("\n") else content
        lines = body.split("\n") if body != "" else []
        total_lines = len(lines)

        meta_base = {
            "original_length": len(content),
            "max_length": cap,
            "total_lines": total_lines,
        }

        # Empty file: nothing to mark.
        if total_lines == 0:
            meta_base.update({"truncated": False, "is_full_file": True,
                              "shown_start_line": 0, "shown_end_line": 0})
            return content, meta_base

        # Reading past the end: explain rather than silently return "".
        if start > total_lines:
            msg = (f"[offset {start} is past the end of this file, which has "
                   f"{total_lines} lines; call file_read with "
                   f"offset={total_lines} to read the last line]")
            meta_base.update({"truncated": True, "is_full_file": False,
                              "shown_start_line": 0, "shown_end_line": 0,
                              "offset_past_eof": True})
            return msg, meta_base

        end = total_lines if lim is None else min(total_lines, start - 1 + lim)
        window = lines[start - 1:end]

        # Apply the character cap at a line boundary.
        shown: list[str] = []
        used = 0
        cap_hit = False
        for ln in window:
            add = len(ln) + (1 if shown else 0)
            if used + add > cap:
                if not shown:  # single line longer than the whole budget
                    shown.append(ln[:cap])
                    used = cap
                cap_hit = True
                break
            shown.append(ln)
            used += add

        shown_start = start
        shown_end = start + len(shown) - 1 if shown else start - 1

        # Everything requested, and it is the whole file -> no marker.
        is_full = (not cap_hit) and shown_start == 1 and shown_end == total_lines
        if is_full:
            meta_base.update({"truncated": False, "is_full_file": True,
                              "shown_start_line": 1, "shown_end_line": total_lines})
            return content, meta_base

        # Something was withheld: emit an explicit, executable marker.
        if cap_hit and shown_end >= total_lines:
            # A single over-long line was clipped mid-line.
            omitted = len(lines[shown_start - 1]) - len(shown[-1])
            marker = (f"\n\n[truncated: line {shown_start} of {total_lines} is "
                      f"longer than the {cap}-char cap; showing its first "
                      f"{len(shown[-1])} characters, {omitted} omitted. "
                      f"Re-read with limit=1 is not enough — split the line or "
                      f"read the file another way.]")
        elif shown_end < total_lines:
            marker = (f"\n\n[truncated: showing lines {shown_start}-{shown_end} "
                      f"of {total_lines}; call file_read with "
                      f"offset={shown_end + 1} to continue]")
        else:  # continuation window that reaches the end of the file
            marker = (f"\n\n[showing lines {shown_start}-{shown_end} of "
                      f"{total_lines}; end of file]")

        output = "\n".join(shown) + marker
        meta_base.update({
            "truncated": True,
            "is_full_file": False,
            "shown_start_line": shown_start,
            "shown_end_line": shown_end,
            "shown_chars": used,
        })
        return output, meta_base

    def _read_text_with_notice(self, file_path) -> tuple[str, Optional[str]]:
        """Decode a file, surfacing (not hiding) undecodable bytes.

        Reads raw bytes and decodes strictly first. Only if the bytes are
        not valid UTF-8 do we fall back to ``errors="replace"`` — and then
        we return a notice stating how many characters were replaced, so
        the loss is never silent. CRLF / lone-CR line endings are
        normalised to LF (line structure is preserved).
        """
        raw = file_path.read_bytes()
        replaced = 0
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("utf-8", errors="replace")
            replaced = content.count("\ufffd")
        # Normalise line endings so line numbers match editors / wc -l.
        if "\r" in content:
            content = content.replace("\r\n", "\n").replace("\r", "\n")
        notice = None
        if replaced:
            notice = (f"\n\n[decode notice: this file is not valid UTF-8; "
                      f"{replaced} undecodable byte(s) were shown as the "
                      f"U+FFFD replacement character — some content may be "
                      f"unrecoverable]")
        return content, notice

    async def execute(self, path: str = "", offset: Any = 1,
                      limit: Any = None, **kwargs) -> ToolResult:
        from kairos.tools.cache import get_cache, make_key
        cache = get_cache()
        # offset/limit MUST be part of the key or a continuation read would
        # return the cached first window.
        ck = make_key(self.name, path=path, offset=offset, limit=limit)
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            file_path = self._resolve_safe(path)
        except PermissionError as e:
            result = ToolResult(success=False, output="", error=str(e))
            cache.set(ck, result)
            return result

        if not file_path.exists():
            result = ToolResult(success=False, output="", error=f"File not found: {path}")
            cache.set(ck, result)
            return result

        if file_path.is_dir():
            # Reading a directory as text raises PermissionError on POSIX
            # and "Is a directory" on Windows — neither is useful for the
            # LLM. Return an actionable hint instead.
            try:
                entries = sorted(p.name + ("/" if p.is_dir() else "")
                                  for p in file_path.iterdir())
                shown = entries[:self.max_dir_entries]
                listing = "\n".join(shown)
                marker = ""
                if len(entries) > self.max_dir_entries:
                    # Old code silently dropped everything past 200 entries.
                    marker = (f"\n\n[truncated: showing {len(shown)} of "
                              f"{len(entries)} entries; narrow the path to "
                              f"see the rest]")
                result = ToolResult(
                    success=True,
                    output=f"<directory listing of {file_path}>\n{listing}{marker}",
                    metadata={"path": str(file_path), "is_dir": True,
                              "entry_count": len(entries),
                              "entries_shown": len(shown),
                              "truncated": bool(marker)},
                )
            except Exception as e:
                result = ToolResult(
                    success=False, output="",
                    error=f"Path is a directory and could not be listed: {e}",
                )
            cache.set(ck, result)
            return result

        try:
            content, decode_notice = self._read_text_with_notice(file_path)
            output, meta = self._window_and_mark(content, offset, limit)
            if decode_notice:
                output = output + decode_notice
                meta["decode_replaced_chars"] = True
            result = ToolResult(
                success=True,
                output=output,
                metadata={"path": str(file_path), **meta},
            )
            cache.set(ck, result)
            return result
        except Exception as e:
            result = ToolResult(success=False, output="", error=str(e))
            cache.set(ck, result)
            return result
